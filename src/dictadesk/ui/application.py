# SPDX-License-Identifier: GPL-3.0-only
"""GTK application lifecycle and desktop delivery; no speech code on the UI thread."""

import os
import sys
import time
from pathlib import Path

from .. import APP_ID, __version__
from ..core.credentials import save_key
from ..core.session import Session
from ..core.settings import private_write
from ..core.models import LOCAL_MODELS
from ..desktop.delivery import destination, paste
from ..desktop.keyboard import Keyboard
from ..desktop.startup import set_enabled
from ..desktop.tray import Tray
from .gtk import Adw, Gdk, Gio, GLib, Gtk
from .microphone import MicrophoneDialog
from .preferences import PreferencesDialog
from .window import Window


class Application(Adw.Application):
    def __init__(self, *, isolated=False, session_factory=Session):
        super().__init__(
            application_id=APP_ID + (".Test" if isolated else ""),
            flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE,
        )
        self.add_main_option(
            "background",
            0,
            GLib.OptionFlags.NONE,
            GLib.OptionArg.NONE,
            "Start in the notification area without opening a window",
            None,
        )
        self.background_launch = False
        self.background_timer = None
        self.isolated, self.session_factory = isolated, session_factory
        self.notifications_enabled = not isolated
        self.window = self.session = self.keyboard = self.tray = None
        self.preferences_dialog = self.microphone_dialog = None
        self.shortcut_problem = ""
        self.escape_problem = ""
        self.target = None
        self.target_lost = False
        self.shutting_down = False
        self.timer = None
        self.last_tray = None
        self.pending_start = None
        self.last_problem = None
        self.environment_problems = {}
        self.resources = Path(__file__).resolve().parents[1] / "resources"

    def do_startup(self):
        Adw.Application.do_startup(self)
        actions = {
            "show": self.present_main,
            "record": self.record_from_button,
            "continuous": lambda: self.record_from_button(continuous=True),
            "cancel": self.cancel_operation,
            "escape": self.escape_pressed,
            "load": lambda: self.session.load(),
            "repair": self.repair_model,
            "preferences": self.show_preferences,
            "about": self.show_about,
            "quit": self.request_quit,
            "transcript": self.show_transcript,
            "copy": self.copy_result,
            "copy-pending": self.copy_pending,
            "undo": self.undo_corrections,
            "paste-help": self.paste_help,
            "save-transcript": self.export,
            "recover": self.recover,
        }
        for name, handler in actions.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda action, param, handler=handler: handler())
            self.add_action(action)
        self.set_accels_for_action("app.preferences", ["<Control>comma"])
        self.set_accels_for_action("app.quit", ["<Control>q"])
        self.set_accels_for_action("app.escape", ["Escape"])
        self.set_accels_for_action("app.copy", ["<Control><Shift>c"])
        theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
        theme.add_search_path(str(self.resources))
        Gtk.Window.set_default_icon_name(APP_ID)

    def dispatch(self, callback):
        def run():
            callback()
            return False

        GLib.idle_add(run)

    def do_command_line(self, command_line):
        self.background_launch = command_line.get_options_dict().contains("background")
        try:
            self.activate()
        finally:
            self.background_launch = False
        return 0

    def do_activate(self):
        if self.window is not None:
            if not self.background_launch:
                self.present_main()
            return
        self.session = self.session_factory(self.dispatch, self.refresh, self.deliver)
        self.session.stream_delivered = self.deliver_segment
        self.session.recording_event = self.notify_recording
        self.window = Window(self)
        self.hold()
        if not self.isolated:
            if os.environ.get("XDG_SESSION_TYPE") == "wayland":
                self.shortcut_problem = "Global shortcuts and automatic paste currently require an X11 session. Button recordings can still be copied."
            else:
                try:
                    self.keyboard = Keyboard(self.key_pressed, self.key_released)
                    self.bind_shortcuts()
                except Exception:
                    self.shortcut_problem = "Desktop shortcuts are unavailable. Button recordings can still be copied."
            try:
                self.tray = Tray(self, self.resources)
            except GLib.Error:
                self.tray = None
        self.timer = GLib.timeout_add(40, self.tick)
        self.session.scan_devices()
        self.refresh()
        if self.background_launch:
            # Login startup must never map the window before the panel is ready.
            # Allow the session's tray host time to register, then fall back only
            # if there is no tray through which the user can reach the app.
            self.background_timer = GLib.timeout_add(5000, self.background_fallback)
        else:
            self.present_main()
        self.session.load_available_model()

    def background_fallback(self):
        self.background_timer = None
        if not self.shutting_down and not (self.tray and self.tray.ready):
            self.present_main()
        return False

    def present_main(self):
        if self.background_timer:
            GLib.source_remove(self.background_timer)
            self.background_timer = None
        if self.window and not self.shutting_down:
            self.window.present()

    def bind_shortcuts(self):
        if self.keyboard and self.session and not self.shutting_down:
            try:
                p = self.session.preferences
                self.keyboard.configure(
                    {
                        "record": p.shortcut,
                        "continuous": p.continuous_shortcut,
                        "cancel": p.cancel_shortcut,
                        "paste": p.paste_shortcut,
                    }
                )
                self.shortcut_problem = " ".join(self.keyboard.errors.values())
            except Exception as exc:
                self.shortcut_problem = str(exc)
            self.refresh()

    def notify_issue(self, title, body, action="app.preferences", key="setup"):
        if not self.notifications_enabled or self.shutting_down:
            return
        notification = Gio.Notification.new(title)
        notification.set_body(body)
        notification.set_icon(Gio.ThemedIcon.new(APP_ID))
        notification.set_default_action(action)
        label = {
            "app.preferences": "Preferences",
            "app.transcript": "Last Transcript",
            "app.show": "Open DictaDesk",
        }[action]
        notification.add_button(label, action)
        try:
            self.send_notification(key, notification)
        except GLib.Error:
            # Preferences retains the problem if desktop notifications are unavailable.
            pass

    def report_session_problem(self):
        problem = self.session.problem
        if not problem or problem == self.last_problem:
            return
        self.last_problem = problem
        _, category, reason = problem
        action = "app.preferences"
        if reason.startswith("No speech"):
            title, body, action = "No speech detected", "Try again.", "app.show"
        elif category == "delivery":
            title, body = "Couldn’t insert text", "Recover it from Last Transcript."
            action = "app.transcript" if self.session.result.text else "app.preferences"
        elif category in ("recording", "starting"):
            title = "Dictation stopped"
            if self.session.pending_text or self.session.result.text:
                body, action = "Available text is in Last Transcript.", "app.transcript"
            else:
                body = "Check the details in Preferences."
        else:
            title = {
                "loading": "Model couldn’t load",
                "saving": "Settings weren’t saved",
                "removing_models": "Models weren’t removed",
                "unloading": "Model couldn’t unload",
                "storage": "Storage needs attention",
            }.get(category, "Settings need attention")
            body = "Check the details in Preferences."
        key = "recording" if category in ("delivery", "recording", "starting") else "setup"
        self.notify_issue(title, body, action, key)

    def report_environment_problems(self):
        c = self.session
        problems = {
            "shortcuts": (
                self.shortcut_problem or self.escape_problem,
                "Shortcut unavailable",
                "Check Keyboard in Preferences.",
            ),
            "microphone": (
                "" if c.microphone_problem == "Checking microphone…" else c.microphone_problem,
                "Microphone unavailable",
                "Choose or test a microphone in Preferences.",
            ),
            "settings": (
                c.store.problem,
                "Settings need recovery",
                "Open DictaDesk to back up and reset them.",
            ),
        }
        for kind, (reason, title, body) in problems.items():
            if reason and reason != self.environment_problems.get(kind):
                self.notify_issue(
                    title,
                    body,
                    "app.show" if kind == "settings" else "app.preferences",
                    "setup-" + kind,
                )
            self.environment_problems[kind] = reason

    def explain_blocked_start(self):
        c = self.session
        if c.pending_text:
            self.notify_issue(
                "Uninserted text is waiting",
                "Recover it before recording again.",
                "app.transcript",
            )
        elif c.store.problem:
            self.notify_issue(
                "Settings need recovery",
                "Open DictaDesk to back up and reset them.",
                "app.show",
            )
        elif c.microphone_problem:
            self.notify_issue(
                "Microphone unavailable", "Choose or test a microphone in Preferences."
            )
        elif c.preferences.backend == "openai" and not c.credential_available:
            self.notify_issue(
                "Remote transcription needs setup", "Add your API key in Preferences."
            )
        elif c.model_needed and not c.problem:
            self.notify_issue("Download a speech model", "Choose a local model in Preferences.")
        else:
            self.report_session_problem()

    def notify_recording(self, kind, body=""):
        if kind == "failed" and self.session.problem:
            self.report_session_problem()
            return
        if not self.notifications_enabled:
            return
        titles = {
            "started": "Recording started",
            "stopped": "Recording stopped",
            "completed": "Dictation finished",
            "attention": "Dictation needs attention",
            "cancelled": "Recording cancelled",
            "failed": "Dictation could not finish",
        }
        messages = {
            "started": "DictaDesk is listening.",
            "stopped": "Finishing transcription…",
            "completed": body or "Your dictation is complete.",
            "attention": "Some text was not inserted. Open DictaDesk to recover it.",
            "cancelled": "No additional text will be copied or inserted.",
            "failed": body or "Check the details in Preferences.",
        }
        if kind == "failed" and body:
            if body.startswith("No speech"):
                titles[kind], messages[kind] = "No speech detected", "Try again."
            else:
                titles[kind], messages[kind] = (
                    "Couldn’t insert text",
                    "Recover it from Last Transcript.",
                )
        notification = Gio.Notification.new(titles[kind])
        notification.set_body(messages[kind])
        notification.set_icon(Gio.ThemedIcon.new(APP_ID))
        notification.set_default_action("app.transcript" if kind == "attention" else "app.show")
        # Replace the current lifecycle notification instead of filling history.
        self.send_notification("recording", notification)

    def show_transcript(self):
        self.window.transcript_dialog.present(self.window)

    def cancel_operation(self):
        self.pending_start = None
        self.session.cancel()

    def record_from_button(self, continuous=False):
        c = self.session
        if c.phase == "listening":
            c.finish_recording()
            return
        if self.pending_start and self.pending_start[0].startswith("button-"):
            self.pending_start = None
            c.notice = "Start cancelled. The model will remain available once loaded."
            self.refresh()
            return
        if c.pending_text:
            c.notice = "Copy or paste the uninserted text before starting a new recording."
            self.show_transcript()
            self.refresh()
            return
        if c.store.problem:
            self.present_main()
            return
        if c.phase == "loading":
            self.pending_start = ("button-continuous" if continuous else "button-record", None)
            self.refresh()
            return
        if c.busy:
            return
        if c.microphone_problem or (
            c.preferences.backend == "openai" and not c.credential_available
        ):
            c.notice = c.microphone_problem or "Add an OpenAI API key in Preferences."
            self.show_preferences()
            self.refresh()
            return
        if c.model_needed:
            self.pending_start = ("button-continuous" if continuous else "button-record", None)
            c.load_available_model()
            if c.phase == "loading":
                return
            self.pending_start = None
            c.notice = "Choose and download a model in Preferences to begin."
            self.show_preferences()
            self.refresh()
            return
        self.start_button_recording(continuous)

    def start_button_recording(self, continuous=False):
        self.target = None
        self.target_lost = False
        self.window.show_inline_transcript()
        self.session.record_in_window(continuous=continuous)

    def key_pressed(self, name):
        c = self.session
        if name == "finish":
            self.escape_pressed()
        elif name == "cancel":
            self.pending_start = None
            c.cancel()
        elif name == "continuous":
            if self.pending_start and self.pending_start[0] == "continuous":
                self.pending_start = None
                self.refresh()
            elif c.continuous and c.phase in ("starting", "listening"):
                c.finish_recording()
            elif not c.busy:
                self.start_from_shortcut(name)
        elif name == "record" and (not c.continuous or c.pause_dictation):
            if self.pending_start and self.pending_start[0] == "record":
                self.pending_start = None
                self.refresh()
            elif c.phase in ("starting", "listening"):
                c.finish_recording()
            elif not c.busy:
                self.start_from_shortcut(name)
        elif name == "paste" and not c.busy and c.result.text:
            if c._begin("delivering") is None:
                return
            self.target = destination(self.keyboard.connection)
            self.target_lost = False
            self.deliver(
                c.pending_text or c.result.text, self.target, pending=bool(c.pending_text)
            )

    def escape_pressed(self):
        if self.pending_start:
            self.pending_start = None
            self.session.notice = (
                "Start cancelled. The model will remain available once loaded."
            )
            self.refresh()
        elif self.session.continuous or self.session.in_window:
            self.session.finish_recording()
        else:
            self.session.cancel()

    def start_from_shortcut(self, name):
        self.window.hide_inline_transcript()
        c = self.session
        self.target = destination(self.keyboard.connection)
        self.target_lost = False
        if c.model_needed and not c.store.problem:
            self.pending_start = (name, self.target)
            c.load_available_model()
            if c.phase == "loading":
                return
            self.pending_start = None
        if not c.can_record or c.pending_text:
            self.explain_blocked_start()
            return
        if name == "continuous":
            c.record_continuous(target=self.target)
        else:
            c.record_continuous(target=self.target, pause_mode=True)

    def resume_pending_start(self):
        c = self.session
        if not self.pending_start or c.busy:
            return
        name, target = self.pending_start
        self.pending_start = None
        if not c.can_record or self.shutting_down:
            if not self.shutting_down:
                self.explain_blocked_start()
            self.refresh()
            return
        if name.startswith("button-"):
            self.start_button_recording(name == "button-continuous")
            return
        if (
            not self.keyboard
            or self.target_lost
            or destination(self.keyboard.connection) != target
        ):
            c.notice = "Destination changed while loading. Press the dictation shortcut in the intended text field."
            self.notify_issue(
                "Recording didn’t start",
                "Select a text field and use the shortcut again.",
                "app.show",
            )
            self.refresh()
            return
        if name == "continuous":
            c.record_continuous(target=target)
        else:
            c.record_continuous(target=target, pause_mode=True)

    def key_released(self, name):
        # All recording shortcuts toggle on press; release only ends the key grab.
        pass

    def tick(self):
        if self.shutting_down:
            self.timer = None
            return False
        if self.keyboard:
            try:
                self.keyboard.poll()
                if (
                    self.target
                    and self.session.busy
                    and destination(self.keyboard.connection) != self.target
                ):
                    if not self.target_lost and self.session.continuous:
                        self.session.finish_recording()
                        self.session.notice = "Focus changed. Stopping continuous dictation; uninserted text will be kept."
                        self.refresh()
                    self.target_lost = True
            except Exception:
                self.shortcut_problem = (
                    "The desktop keyboard connection was lost. Restart DictaDesk to reconnect."
                )
                self.keyboard.close()
                self.keyboard = None
                self.target_lost = True
                if self.session.continuous:
                    self.session.finish_recording()
                self.refresh()
        self.session.tick()
        self.resume_pending_start()
        if self.microphone_dialog:
            self.microphone_dialog.refresh()
        return True

    def refresh(self):
        if not self.window:
            return
        c = self.session
        if self.keyboard:
            self.keyboard.set_escape(
                (c.continuous or c.in_window or bool(self.pending_start))
                and not self.shutting_down
            )
            self.escape_problem = getattr(self.keyboard, "errors", {}).get("finish", "")
        available = not self.shutting_down
        enabled = {
            "record": not c.busy
            or c.phase == "loading"
            or (c.phase == "listening" and not (c.capture and c.capture.stop.is_set())),
            "continuous": not c.busy
            or c.phase == "loading"
            or (c.continuous and c.phase == "listening"),
            "cancel": c.busy
            and c.phase not in ("cancelling", "saving", "unloading", "removing_models"),
            "escape": c.busy
            and c.phase not in ("cancelling", "saving", "unloading", "removing_models"),
            "load": not c.busy and not c.store.problem,
            "repair": not c.busy and not c.store.problem,
            "preferences": not c.busy,
            "transcript": bool(c.result.text or c.pending_text),
            "copy": bool(c.result.text) and not c.continuous,
            "copy-pending": bool(c.pending_text) and not c.busy,
            "undo": bool(c.result.can_undo) and not c.busy,
            "paste-help": bool(c.result.text) and not c.busy,
            "save-transcript": bool(c.result.text) and not c.busy,
            "recover": bool(c.store.damaged) and not c.busy,
        }
        for name, value in enabled.items():
            self.lookup_action(name).set_enabled(value and available)
        self.window.refresh()
        self.report_session_problem()
        self.report_environment_problems()
        if self.preferences_dialog:
            self.preferences_dialog.refresh()
        if self.microphone_dialog:
            self.microphone_dialog.refresh()
        key = (c.state, c.busy, c.preferences.symbolic)
        if self.tray and key != self.last_tray:
            shape = {
                "Ready": "idle",
                "Standby": "idle",
                "Loading": "loading",
                "Setup needed": "unavailable",
                "Listening": "listening",
                "Transcribing": "processing",
            }[c.state]
            self.tray.update(shape, c.state, c.preferences.symbolic)
            self.last_tray = key

    def copy_result(self):
        if self.session.result.text:
            Gdk.Display.get_default().get_clipboard().set(self.session.result.text)
            self.session.pending_text = ""
            self.session.notice = "Transcript copied."
            self.refresh()

    def copy_pending(self):
        if self.session.pending_text:
            Gdk.Display.get_default().get_clipboard().set(self.session.pending_text)
            self.session.pending_text = ""
            self.session.notice = "Uninserted text copied. Paste it into your document before starting another session."
            self.refresh()

    def deliver_segment(self, value, target, done):
        """One committed phrase; never deliver the accumulating full transcript."""
        c = self.session
        generation, cancelled = c.operation, c.cancelled
        started = time.monotonic()

        def attempt():
            try:
                invalid = (
                    self.shutting_down
                    or generation != c.operation
                    or cancelled.is_set()
                    or not self.keyboard
                    or self.target_lost
                    or destination(self.keyboard.connection) != target
                )
                if invalid or time.monotonic() - started > 3:
                    self.target_lost = True
                    c.finish_recording()
                    done(False)
                    return False
                if self.keyboard.down or self.keyboard.modifiers_down():
                    return True
                Gdk.Display.get_default().get_clipboard().set(value)
                sent = paste(self.keyboard.connection, target)
                if not sent:
                    self.target_lost = True
                    c.finish_recording()

                # Let the destination request the clipboard before another phrase
                # can replace it. X11 cannot acknowledge application-level insertion.
                def acknowledge():
                    done(sent)
                    return False

                GLib.timeout_add(250, acknowledge)
            except Exception:
                self.target_lost = True
                c.finish_recording()
                done(False)
            return False

        GLib.timeout_add(50, attempt)

    def deliver(self, value, target, pending=False):
        c = self.session
        Gdk.Display.get_default().get_clipboard().set(value)
        if not target or not self.keyboard or self.target_lost:
            c.phase = "idle"
            c.notice = (
                "Transcript copied."
                if target is None
                else "Destination changed. Transcript copied; use Paste Again in the intended field."
            )
            if target is not None:
                self.delivery_attention()
            return
        ticket = c.operation
        cancelled = c.cancelled
        start = time.monotonic()
        c.phase = "delivering"
        c.notice = "Release the shortcut to insert the transcript."
        self.refresh()

        def try_paste():
            if self.shutting_down or ticket != c.operation:
                return False
            inserted = False
            if cancelled.is_set():
                c.phase = "idle"
                c.notice = "Cancelled. Nothing was inserted."
            elif (
                not self.keyboard
                or self.target_lost
                or destination(self.keyboard.connection) != target
            ):
                c.phase = "idle"
                c.notice = "Destination changed. Transcript copied; use Paste Again in the intended field."
            elif time.monotonic() - start > 3:
                c.phase = "idle"
                c.notice = "Keys are still held. Transcript copied; use Paste Again when ready."
            elif self.keyboard.down or self.keyboard.modifiers_down():
                return True
            else:
                c.phase = "idle"
                sent = paste(self.keyboard.connection, target)
                inserted = sent
                if sent and pending:
                    c.pending_text = ""
                c.notice = (
                    "Transcript pasted."
                    if sent
                    else "Transcript copied. Paste into the intended field."
                )
            if cancelled.is_set():
                c._recording_event(ticket, "cancelled")
            elif not inserted:
                self.delivery_attention()
            else:
                c._recording_event(
                    ticket, "completed", "Your text has been copied to the clipboard."
                )
            self.refresh()
            return False

        def attempt():
            try:
                return try_paste()
            except Exception:
                c.phase = "idle"
                c.set_problem(
                    "delivery",
                    "Automatic paste failed. Your transcript is still available to copy.",
                )
                c._recording_event(ticket, "failed")
                self.refresh()
                return False

        GLib.timeout_add(50, attempt)

    def delivery_attention(self):
        c = self.session
        if c._dictation_operation == c.operation:
            c._recording_event(c.operation, "attention")
        else:
            self.notify_issue(
                "Text wasn’t inserted",
                "Recover it from Last Transcript.",
                "app.transcript",
                "delivery",
            )

    def undo_corrections(self):
        c = self.session
        if c.result.can_undo:
            c.result.undo = not c.result.undo
            c.notice = (
                "Transcript changes undone. The original wording is restored here; previously pasted text is unchanged."
                if c.result.undo
                else "Transcript changes restored."
            )
            self.refresh()

    def paste_help(self):
        from ..desktop.keyboard import Chord

        self.alert(
            "Choose Where to Paste",
            f"Close this message, click the intended text field, then press {Chord.parse(self.session.preferences.paste_shortcut)}. This pastes uninserted text when available; otherwise it pastes the full last transcript. The app checks that the destination stays focused before sending Paste.",
        )

    def dialog_parent(self):
        if self.preferences_dialog and self.preferences_dialog.dialog.get_visible():
            return self.preferences_dialog.dialog
        return self.window

    def alert(self, heading, body, parent=None):
        dialog = Adw.AlertDialog(heading=heading, body=body)
        dialog.add_response("close", "Close")
        dialog.present(parent or self.dialog_parent())
        return dialog

    def show_preferences(self):
        if self.session.busy:
            return
        if self.preferences_dialog is None:
            self.preferences_dialog = PreferencesDialog(self)
        self.preferences_dialog.present()

    def save_preferences(self, prefs, key, close):
        def prepare():
            if key:
                save_key(key)

        def complete():
            try:
                set_enabled(prefs.autostart)
            except Exception as exc:
                self.session.set_problem(
                    "saving",
                    f"Preferences saved, but login startup could not be changed: {exc}",
                )
            close()

        self.session.save_preferences(prefs, complete, prepare=prepare)

    def show_microphone_test(self):
        if self.session.busy:
            return
        if self.microphone_dialog is None:
            self.microphone_dialog = MicrophoneDialog(self)
        self.microphone_dialog.present()

    def confirm_download(self, precision, model=None):
        model = model or self.session.preferences.local_model
        dialog = Adw.AlertDialog(
            heading="Download the Model Again?",
            body=f"Download a replacement for {LOCAL_MODELS[model]['name']} ({precision}). The previous model remains as a backup until you remove downloaded models.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("download", "Download Again")
        dialog.set_response_appearance("download", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect(
            "response",
            lambda _, response: (
                self.session.download_model(precision, repair=True, model=model)
                if response == "download"
                else None
            ),
        )
        dialog.present(self.dialog_parent())

    def remove_models(self, model=None):
        dialog = Adw.AlertDialog(
            heading="Remove Downloaded Models?",
            body=(
                f"Remove all {LOCAL_MODELS[model]['name']} downloads and backups? Other models, preferences and transcripts will remain."
                if model
                else "Remove all local model downloads and backups for your account. Your preferences and last transcript will remain. You can download a model again later."
            ),
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("remove", "Remove Models")
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect(
            "response",
            lambda _, response: (
                self.session.remove_models(model) if response == "remove" else None
            ),
        )
        dialog.present(self.dialog_parent())

    def repair_model(self, parent=None):
        dialog = Adw.AlertDialog(
            heading="Download the Model Again?",
            body="This downloads a fresh local speech model. Existing model files stay in a backup folder until you choose to remove them.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("repair", "Download Again")
        dialog.set_response_appearance("repair", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect(
            "response",
            lambda _, response: (
                self.session.load(repair=True) if response == "repair" else None
            ),
        )
        dialog.present(self.dialog_parent())

    def recover(self):
        try:
            backup = self.session.store.recover()
            self.session.preferences = self.session.store.read()
            self.session.notice = (
                f"Settings backed up to {backup.name}. Fresh settings are ready."
            )
            self.bind_shortcuts()
            self.session.scan_devices()
        except Exception as exc:
            self.session.set_problem("saving", str(exc))
        self.refresh()

    def export(self):
        value = self.session.result.text
        dialog = Gtk.FileDialog(title="Save Transcript", initial_name="transcript.txt")

        def chosen(file_dialog, result):
            try:
                file = file_dialog.save_finish(result)
                path = file.get_path()
                if not path:
                    raise ValueError("Choose a local file for this private transcript.")
                private_write(path, (value + "\n").encode())
                self.session.notice = "Transcript saved."
                self.refresh()
            except GLib.Error as exc:
                if exc.code != Gtk.DialogError.DISMISSED:
                    self.alert("Transcript Not Saved", str(exc))
            except Exception as exc:
                self.alert("Transcript Not Saved", str(exc))

        dialog.save(self.window, None, chosen)

    def show_about(self):
        dialog = Adw.AboutDialog(
            application_name="DictaDesk",
            application_icon=APP_ID,
            version=__version__,
            developer_name="Erick Woods",
            copyright="Copyright © 2026 Erick Woods",
            license_type=Gtk.License.GPL_3_0_ONLY,
            comments="Voice dictation for everyday applications on the Cinnamon desktop.",
        )
        dialog.add_credit_section("Contributors", ["Erick Woods"])
        dialog.present(self.window)

    def request_quit(self):
        if self.shutting_down:
            return
        self.shutting_down = True
        if self.background_timer:
            GLib.source_remove(self.background_timer)
            self.background_timer = None
        self.refresh()
        if self.keyboard:
            self.keyboard.close()
            self.keyboard = None

        def done():
            if self.tray:
                self.tray.close()
            if self.timer:
                GLib.source_remove(self.timer)
                self.timer = None
            self.release()
            self.quit()

        self.session.close(done)


def main():
    os.umask(0o077)
    app = Application()
    raise SystemExit(app.run(sys.argv))
