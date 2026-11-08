# SPDX-License-Identifier: GPL-3.0-only
"""GTK application lifecycle and desktop delivery; no speech code on the UI thread."""

from dataclasses import replace
import os
import sys
import time
from pathlib import Path

from .. import APP_ID, __version__
from ..core.credentials import save_key
from ..core.session import Session
from ..core.handsfree import HandsFree
from ..core.voice_commands import diagnostic, exact_application_match, match_applications
from ..core.settings import private_write
from ..core.models import LOCAL_MODELS
from ..desktop.delivery import destination, paste
from ..desktop.destination import DestinationTracker, Target
from ..desktop.keyboard import Keyboard
from ..desktop.startup import set_enabled
from ..desktop.tray import Tray
from ..desktop.lock import ScreenLock
from ..desktop.commands import DesktopCommands
from .gtk import Adw, Gdk, Gio, GLib, Gtk
from .microphone import MicrophoneDialog
from .preferences import PreferencesDialog
from .transcript import TranscriptWindow
from .usage import UsageWindow
from .command_chooser import CommandChooser


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
        self.isolated, self.session_factory = isolated, session_factory
        self.notifications_enabled = not isolated
        self.session = self.keyboard = self.tray = None
        self.destinations = None
        self.transcript_window = None
        self.usage_window = None
        self.alert_windows = set()
        self.preferences_dialog = self.microphone_dialog = None
        self.shortcut_problem = ""
        self.escape_problem = ""
        self.shutting_down = False
        self.timer = None
        self.last_tray = None
        self.pending_start = None
        self.handsfree = None
        self.screen_lock = None
        self.voice_toggle_target = None
        self.command_chooser = None
        self.desktop_commands = DesktopCommands(self.dispatch)
        self.voice_view = None
        self.last_problem = None
        self.environment_problems = {}
        self.resources = Path(__file__).resolve().parents[1] / "resources"

    def do_startup(self):
        Adw.Application.do_startup(self)
        actions = {
            "show": self.show_preferences,
            "record": lambda: self.start_from_tray("record"),
            "continuous": lambda: self.start_from_tray("continuous"),
            "cancel": self.cancel_operation,
            "escape": self.escape_pressed,
            "load": lambda: self.session.load(),
            "repair": self.repair_model,
            "preferences": self.show_preferences,
            "hands-free": self.toggle_hands_free,
            "pause-commands": self.toggle_voice_pause,
            "about": self.show_about,
            "usage": self.show_usage,
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
        self.activate()
        return 0

    def do_activate(self):
        if self.session is not None:
            return
        self.session = self.session_factory(self.dispatch, self.refresh, self.deliver)
        self.handsfree = HandsFree(
            self.session,
            self.refresh,
            self.launch_command,
            self.command_feedback,
            desktop_action=self.run_desktop_command,
            capture_destination=self.capture_command_destination,
        )
        self.session.handsfree = self.handsfree
        self.session.stream_delivered = self.deliver_segment
        self.session.recording_event = self.notify_recording
        self.hold()
        self.desktop_commands.start()
        if not self.isolated:
            self.screen_lock_changed(True, "Desktop lock status unavailable")
            try:
                self.screen_lock = ScreenLock(self.screen_lock_changed)
            except GLib.Error:
                pass
            if os.environ.get("XDG_SESSION_TYPE") == "wayland":
                self.shortcut_problem = "Global shortcuts and automatic paste currently require an X11 session. Transcripts remain available from the tray."
            else:
                try:
                    self.keyboard = Keyboard(self.key_pressed, self.key_released)
                    self.destinations = DestinationTracker(self.keyboard.connection)
                    self.bind_shortcuts()
                except Exception:
                    self.shortcut_problem = "Desktop shortcuts are unavailable. Check your X11 session and restart DictaDesk."
            try:
                self.tray = Tray(self, self.resources)
            except GLib.Error:
                self.tray = None
        self.timer = GLib.timeout_add(40, self.tick)
        self.session.scan_devices()
        self.refresh()
        self.session.load_available_model()

    def screen_lock_changed(self, blocked, reason):
        c = self.session
        # Unknown lock status blocks unattended commands. A confirmed lock also
        # stops dictation, remaining latched if the lock service then disappears.
        c.desktop_locked = blocked and (reason == "Screen locked" or c.desktop_locked)
        c.desktop_blocked, c.desktop_reason = blocked, reason
        if blocked:
            self.handsfree.suspend()
        if c.desktop_locked:
            self.pending_start = None
            if c.phase in ("starting", "listening", "transcribing", "delivering"):
                c.cancel()
        self.refresh()

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
            "app.show": "Preferences",
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
                "Open Preferences to back up and reset them.",
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
                "Open Preferences to back up and reset them.",
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
            "attention": "Some text was not inserted. Recover it from Last Transcript in the tray.",
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
        if self.transcript_window is None:
            self.transcript_window = TranscriptWindow(self)
        self.transcript_window.present()

    def cancel_operation(self):
        if self.handsfree and not self.session.busy:
            self.handsfree.cancel()
        self.pending_start = None
        self.session.cancel()

    def start_from_tray(self, name):
        if self.session.desktop_locked:
            return
        if self.session.phase == "listening":
            self.session.finish_recording()
        elif self.keyboard:
            self.start_from_shortcut(name)
        else:
            self.session.record_continuous(target=Target(None), pause_mode=name == "record")

    def key_pressed(self, name):
        if self.session.desktop_locked:
            return
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
            target = self.destinations.capture(explicit=True)
            self.deliver(c.pending_text or c.result.text, target)

    def escape_pressed(self):
        if self.handsfree and self.handsfree.state == "command" and not self.session.busy:
            self.handsfree.cancel()
            return
        if self.pending_start:
            self.pending_start = None
            self.session.notice = (
                "Start cancelled. The model will remain available once loaded."
            )
            self.refresh()
        elif self.session.phase in ("starting", "listening"):
            self.session.finish_recording()
        else:
            self.session.cancel()

    def start_from_shortcut(self, name):
        if self.session.desktop_locked:
            return
        c = self.session
        target = self.destinations.capture() if self.destinations else Target(None)
        if c.model_needed and not c.store.problem:
            self.pending_start = (name, target)
            c.load_available_model()
            if c.phase == "loading":
                return
            self.pending_start = None
        if not c.can_record or c.pending_text:
            self.explain_blocked_start()
            return
        if name == "continuous":
            c.record_continuous(target=target)
        else:
            c.record_continuous(target=target, pause_mode=True)

    def resume_pending_start(self):
        c = self.session
        if c.desktop_locked:
            self.pending_start = None
            return
        if not self.pending_start or c.busy:
            return
        name, target = self.pending_start
        self.pending_start = None
        if not c.can_record or self.shutting_down:
            if not self.shutting_down:
                self.explain_blocked_start()
            self.refresh()
            return
        if not self.destinations or not self.destinations.focused_on(target):
            c.notice = "Destination changed while loading. Press the dictation shortcut in the intended text field."
            self.notify_issue(
                "Recording didn’t start", "Select a text field and use the shortcut again."
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
            except Exception:
                self.shortcut_problem = (
                    "The desktop keyboard connection was lost. Restart DictaDesk to reconnect."
                )
                self.keyboard.close()
                self.keyboard = None
                if self.session.continuous:
                    self.session.finish_recording()
                self.refresh()
        self.session.tick()
        self.resume_pending_start()
        if self.voice_toggle_target is not None and not self.session.busy:
            target, self.voice_toggle_target = self.voice_toggle_target, None
            prefs = replace(self.session.preferences, hands_free=target)
            self.session.save_preferences(
                prefs, lambda: setattr(self.handsfree, "override_disabled", False)
            )
        self.handsfree.tick()
        voice_view = (
            self.handsfree.state,
            self.handsfree.problem,
            int(time.monotonic() / 0.8) % 2 if self.handsfree.state == "command" else None,
        )
        if voice_view != self.voice_view:
            self.voice_view = voice_view
            self.refresh()
        if self.microphone_dialog:
            self.microphone_dialog.refresh()
        return True

    def refresh(self):
        if not self.session:
            return
        c = self.session
        if self.keyboard:
            self.keyboard.set_escape(
                (
                    c.continuous
                    or bool(self.pending_start)
                    or (self.handsfree and self.handsfree.state == "command")
                )
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
        if self.handsfree and self.handsfree.state == "command":
            enabled["cancel"] = enabled["escape"] = True
        enabled["pause-commands"] = bool(
            self.handsfree and self.handsfree.enabled and not c.busy
        )
        enabled["hands-free"] = c.preferences.backend == "local" and (
            not c.busy or (self.handsfree and self.handsfree.enabled)
        )
        if self.command_chooser and not self.handsfree.valid(self.command_chooser[1]):
            self.command_chooser[0].close()
            self.command_chooser = None
        for name, value in enabled.items():
            self.lookup_action(name).set_enabled(value and available)
        if self.transcript_window:
            self.transcript_window.refresh()
        if self.usage_window:
            self.usage_window.refresh()
        self.report_session_problem()
        self.report_environment_problems()
        if self.preferences_dialog:
            self.preferences_dialog.refresh()
        if self.microphone_dialog:
            self.microphone_dialog.refresh()
        shape = {
            "Ready": "idle",
            "Standby": "idle",
            "Loading": "loading",
            "Setup needed": "unavailable",
            "Listening": "listening",
            "Transcribing": "processing",
        }[c.state]
        title = c.state
        if self.handsfree and self.handsfree.enabled and not c.busy:
            state = self.handsfree.state
            if state == "wake":
                shape, title = "wake", f"Listening locally for “{c.preferences.wake_phrase}”"
            elif state == "command":
                shape = "wake" if int(time.monotonic() / 0.8) % 2 else "idle"
                title = (
                    "Choose an application by number (local). Say cancel or press Escape to stop."
                    if self.handsfree.choice
                    else "Waiting for a command (local). Say cancel or press Escape to stop."
                )
            elif state == "processing":
                shape, title = "processing", "Checking the wake phrase locally"
            elif state == "paused":
                shape, title = "idle", f"Voice commands paused: {self.handsfree.problem}"
            elif state in ("loading", "unavailable"):
                shape = state
                title = self.handsfree.problem or "Loading local voice commands"
        key = (
            shape,
            title,
            c.busy,
            c.preferences.symbolic,
            c.preferences.hands_free,
            bool(self.handsfree and self.handsfree.override_disabled),
            c.preferences.backend,
        )
        if self.tray and key != self.last_tray:
            self.tray.update(shape, title, c.preferences.symbolic)
            self.last_tray = key

    def toggle_hands_free(self):
        c = self.session
        if c.preferences.backend != "local":
            return
        target = not self.handsfree.enabled
        if target:
            from ..core.models import downloaded

            if c.busy or not downloaded(c.preferences.quantization, c.preferences.local_model):
                self.command_feedback("Download a local model in Preferences first.")
                return
        # Disable immediately, even if a settings/model operation is pending.
        self.handsfree.override_disabled = True
        self.handsfree.suspend()
        self.voice_toggle_target = target
        self.refresh()

    def toggle_voice_pause(self):
        if self.handsfree.paused:
            self.handsfree.resume()
        else:
            self.handsfree.pause(30)
        self.refresh()

    def command_feedback(self, message, *, heard=None, query=None):
        details = diagnostic(heard, query) if heard is not None else ""
        if self.notifications_enabled:
            note = Gio.Notification.new("Voice Commands")
            note.set_body(f"{message}\n{details}" if details else message)
            note.set_priority(Gio.NotificationPriority.LOW)
            self.send_notification("voice-commands", note)

    def capture_command_destination(self):
        return self.destinations.capture() if self.destinations else Target(None)

    def run_desktop_command(self, kind, arguments, generation, *, heard=""):
        target = self.handsfree.activation_target

        def valid():
            return not self.shutting_down and self.handsfree.valid(generation)

        def completed(message):
            if valid():
                self.command_feedback(message)
                self.handsfree.complete(generation)

        if not valid():
            return
        if kind == "pause-commands":
            self.handsfree.pause(arguments[0])
            self.command_feedback(self.handsfree.problem)
            return
        if kind == "clipboard":
            action = arguments[0]
            value = self.session.last_dictation
            clipboard = Gdk.Display.get_default().get_clipboard()
            if action == "clear":
                cleared = clipboard.set_content(None)
                completed(
                    "Clipboard cleared. Last dictation retained."
                    if cleared
                    else "The clipboard could not be cleared."
                )
            elif not value:
                completed("No completed dictation is available.")
            elif action == "copy":
                clipboard.set(value)
                completed("Last dictation copied.")
            else:
                c = self.session
                if c.pending_text and c.pending_text != value:
                    completed("Recover uninserted text before pasting another dictation.")
                    return
                # End this activation before beginning the ordinary delivery
                # operation; existing cancellation and recovery remain in charge.
                self.handsfree.complete(generation)
                if c._begin("delivering") is not None:
                    self.deliver(value, target or Target(None))
            return
        self.desktop_commands.execute(kind, arguments, valid, completed, target=target)

    def launch_command(self, query, generation, *, heard=""):
        if not self.handsfree.valid(generation):
            return
        applications = Gio.AppInfo.get_all()
        if query == ("file", "manager"):
            default = Gio.AppInfo.get_default_for_type("inode/directory", False)
            matches = [default] if default else []
        elif query == ("terminal",):
            matches = [
                app
                for app in applications
                if app.get_id()
                in (
                    "org.gnome.Terminal.desktop",
                    "gnome-terminal.desktop",
                    "xfce4-terminal.desktop",
                    "org.kde.konsole.desktop",
                    "mate-terminal.desktop",
                )
            ][:1]
            if not matches:
                matches = match_applications(query, applications)
        else:
            matches = match_applications(query, applications)

        def launch(app):
            if not self.handsfree.valid(generation):
                return
            try:
                app.launch([], Gdk.Display.get_default().get_app_launch_context())
            except GLib.Error:
                self.command_feedback(
                    "The application could not be opened.", heard=heard, query=query
                )
            finally:
                self.handsfree.complete(generation)

        if not matches:
            self.command_feedback("Application not found.", heard=heard, query=query)
            self.handsfree.complete(generation)
        elif len(matches) == 1 and (
            query in (("file", "manager"), ("terminal",))
            or exact_application_match(query, matches[0])
        ):
            launch(matches[0])
        else:
            dialog = CommandChooser(
                self,
                "Did You Mean?" if len(matches) == 1 else "Which Application?",
                diagnostic(heard, query),
            )
            listing, hint = dialog.listing, dialog.hint

            current_page = [-1]

            def render(page):
                current_page[0] = page
                while child := listing.get_first_child():
                    listing.remove(child)
                start = page * 4
                shown = matches[start : start + 4]
                more = start + 4 < len(matches)
                help_text = "Say a number or “cancel”. You can also click a choice."
                if more:
                    help_text = (
                        "Say a number, “more”, or “cancel”. You can also click a choice."
                    )
                hint.set_label(help_text)

                def choose(number, heard_choice=""):
                    if not self.handsfree.valid(generation) or current_page[0] != page:
                        return
                    if number in ("cancel", "timeout"):
                        dialog.close()
                    elif number in (5, "more") and more:
                        render(page + 1)
                    elif isinstance(number, int) and 1 <= number <= len(shown):
                        launch(shown[number - 1])
                        dialog.close()
                    else:
                        hint.set_label(f"{diagnostic(heard_choice)}\n{help_text}")

                for number, app in enumerate(shown, 1):
                    row = Adw.ActionRow(
                        title=f"{number}. {app.get_display_name()}",
                        subtitle=(
                            app.get_id()
                            if sum(
                                other.get_display_name() == app.get_display_name()
                                for other in matches
                            )
                            > 1
                            else ""
                        ),
                        activatable=True,
                        use_markup=False,
                    )
                    row.set_tooltip_text(app.get_id())
                    row.connect("activated", lambda _, number=number: choose(number))
                    listing.append(row)
                if more:
                    row = Adw.ActionRow(title="5. More matches", activatable=True)
                    row.connect("activated", lambda *_: choose("more"))
                    listing.append(row)
                self.handsfree.offer_choices(generation, choose)

            def closed(*_):
                self.handsfree.complete(generation)
                if self.command_chooser and self.command_chooser[0] is dialog:
                    self.command_chooser = None
                return False

            dialog.connect("close-request", closed)
            self.command_chooser = (dialog, generation)
            render(0)
            dialog.present()

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
        """Keep streaming in the original field; defer after any focus change."""
        if (
            not self.destinations
            or getattr(target, "deferred", False)
            or not self.destinations.focused_on(target)
        ):
            if target:
                target.deferred = True
            done(False)
            return
        self.insert(value, target, done, restore=False)

    def deliver(self, value, target):
        c = self.session
        if c.desktop_locked:
            c.pending_text = value
            c.phase = "idle"
            return
        Gdk.Display.get_default().get_clipboard().set(value)
        if target is None:  # Automatic insertion explicitly disabled in settings.
            c.pending_text = ""
            c.phase = "idle"
            c.notice = "Transcript copied."
            return
        ticket = c.operation
        c.phase = "delivering"
        c.notice = "Returning to the original dictation field…"
        self.refresh()

        def completed(inserted):
            if ticket != c.operation or self.shutting_down:
                return
            c.phase = "idle"
            if inserted:
                c.pending_text = ""
                c.notice = "Transcript pasted."
                c._recording_event(
                    ticket, "completed", "Your text was inserted into the original field."
                )
            else:
                c.pending_text = value
                c.notice = "Automatic insertion failed. Text is on the clipboard and in Last Transcript; use Copy Uninserted Text or Paste Again in the intended field."
                if c.cancelled.is_set():
                    c._recording_event(ticket, "cancelled")
                else:
                    self.delivery_attention()
            self.refresh()

        self.insert(value, target, completed, restore=True)

    def insert(self, value, target, done, *, restore):
        """Asynchronous focus/clipboard sequencing, bound to one operation ticket."""
        c = self.session
        generation, cancelled = c.operation, c.cancelled
        started = time.monotonic()
        previous = None
        requested = False
        finished = False

        def complete(sent):
            nonlocal finished
            if finished:
                return
            finished = True
            if not sent and target:
                target.deferred = True
            done(sent)

        def attempt():
            nonlocal previous, requested
            try:
                if (
                    self.shutting_down
                    or c.desktop_locked
                    or cancelled.is_set()
                    or generation != c.operation
                    or not self.keyboard
                    or not self.destinations
                    or time.monotonic() - started > 3
                ):
                    complete(False)
                    return False
                if self.keyboard.down or self.keyboard.modifiers_down():
                    return True
                tracker = self.destinations
                if not tracker.valid(target):
                    complete(False)
                    return False
                if not tracker.focused_on(target):
                    if not restore or (requested and time.monotonic() - started > 2):
                        complete(False)
                        return False
                    if not requested:
                        previous = tracker.capture()
                        requested = True
                    if not tracker.restore(target):
                        complete(False)
                        return False
                    return True
                if not tracker.restore_position(target) or not tracker.focused_on(target):
                    complete(False)
                    return False
                Gdk.Display.get_default().get_clipboard().set(value)
                tracker.expect(target, value)
                # Reuse the existing XTest paste and its final X focus check.
                current = destination(self.keyboard.connection)
                sent = bool(
                    current
                    and current.client == target.window.client
                    and current.pid == target.window.pid
                    and tracker.focused_on(target)
                    and paste(self.keyboard.connection, current)
                )

                pasted_at = time.monotonic()

                def acknowledged():
                    try:
                        if generation != c.operation or cancelled.is_set() or c.desktop_locked:
                            complete(False)
                            return False
                        confirmed = sent and tracker.advance(target)
                        if confirmed is None and time.monotonic() - pasted_at < 1.5:
                            return True
                        if confirmed is None:
                            target.invalid = True
                            confirmed = False
                        if (
                            previous
                            and not self.keyboard.down
                            and not self.keyboard.modifiers_down()
                        ):
                            tracker.return_focus(previous, target)
                        complete(confirmed)
                    except Exception:
                        complete(False)
                    return False

                GLib.timeout_add(250, acknowledged)
            except Exception:
                complete(False)
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
        if self.transcript_window and self.transcript_window.dialog.get_visible():
            return self.transcript_window.dialog
        return None

    def alert(self, heading, body, parent=None):
        dialog = Adw.AlertDialog(heading=heading, body=body)
        dialog.add_response("close", "Close")
        self.present_dialog(dialog, parent)
        return dialog

    def present_dialog(self, dialog, parent=None):
        parent = parent or self.dialog_parent()
        if parent:
            dialog.present(parent)
            return
        # Adw.Dialog needs a real surface; create a temporary utility host only
        # for this requested dialog. Startup never creates a window.
        host = Adw.Window(
            application=self,
            title=dialog.get_title() or "DictaDesk",
            default_width=480,
            default_height=320,
        )
        self.alert_windows.add(host)

        def closed(*_):
            self.alert_windows.discard(host)
            host.destroy()

        dialog.connect("closed", closed)
        host.present()
        dialog.present(host)

    def show_usage(self):
        if self.usage_window is None:
            self.usage_window = UsageWindow(self)
        self.usage_window.present()

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
            if self.voice_toggle_target is not False:
                self.handsfree.override_disabled = False
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
        self.present_dialog(dialog)

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
        self.present_dialog(dialog)

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
        self.present_dialog(dialog)

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

        dialog.save(self.dialog_parent(), None, chosen)

    def show_about(self):
        dialog = Adw.AboutWindow(
            application=self,
            application_name="DictaDesk",
            application_icon=APP_ID,
            version=__version__,
            developer_name="Erick Woods",
            copyright="Copyright © 2026 Erick Woods",
            license_type=Gtk.License.GPL_3_0_ONLY,
            comments="Voice dictation for everyday applications on the Cinnamon desktop.",
        )
        dialog.add_credit_section("Contributors", ["Erick Woods"])
        dialog.present()

    def request_quit(self):
        if self.shutting_down:
            return
        self.shutting_down = True
        self.desktop_commands.close()
        if self.screen_lock:
            self.screen_lock.close()
            self.screen_lock = None
        self.refresh()
        if self.destinations:
            self.destinations.close()
            self.destinations = None
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
