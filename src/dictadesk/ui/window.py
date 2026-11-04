# SPDX-License-Identifier: GPL-3.0-only
"""Dictation-first window; optional transcript tools live in a separate dialog."""

from ..desktop.keyboard import Chord
from ..core.models import LOCAL_MODELS
from .gtk import Adw, Gio, Gtk, Pango


def text(value="", **kwargs):
    return Gtk.Label(
        label=value, xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, **kwargs
    )


def icon_button(icon, name, action):
    button = Gtk.Button(
        icon_name=icon, tooltip_text=name, action_name=action, valign=Gtk.Align.CENTER
    )
    button.update_property([Gtk.AccessibleProperty.LABEL], [name])
    return button


class Window(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(
            application=app, title="DictaDesk", default_width=749, default_height=384
        )
        self.app = app
        self.set_size_request(320, 280)
        toolbar = Adw.ToolbarView()
        self.overlay = Adw.ToastOverlay(child=toolbar)
        self.set_content(self.overlay)
        header = Adw.HeaderBar()
        toolbar.add_top_bar(header)
        menu = Gio.Menu()
        menu.append("Continuous Dictation", "app.continuous")
        menu.append("Last Transcript…", "app.transcript")
        menu.append("Preferences", "app.preferences")
        menu.append("About DictaDesk", "app.about")
        section = Gio.Menu()
        section.append("Quit", "app.quit")
        menu.append_section(None, section)
        header.pack_end(
            Gtk.MenuButton(
                icon_name="open-menu-symbolic", tooltip_text="Main Menu", menu_model=menu
            )
        )
        content = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=24,
            margin_start=18,
            margin_end=18,
            margin_top=18,
            margin_bottom=24,
        )
        self.content = content
        toolbar.set_content(
            Gtk.ScrolledWindow(
                hscrollbar_policy=Gtk.PolicyType.NEVER,
                child=Adw.Clamp(maximum_size=880, child=content),
            )
        )
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        card.add_css_class("card")
        content.append(card)
        top = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            margin_start=18,
            margin_end=18,
            margin_top=18,
            margin_bottom=18,
        )
        card.append(top)
        line = Gtk.Box(spacing=12)
        self.microphone = Gtk.Image(pixel_size=36)
        self.heading = text(hexpand=True)
        self.heading.add_css_class("title-2")
        self.recording_dot = Gtk.Image(
            icon_name="media-record-symbolic",
            pixel_size=12,
            halign=Gtk.Align.END,
            valign=Gtk.Align.END,
            visible=False,
        )
        self.recording_dot.add_css_class("error")
        microphone_overlay = Gtk.Overlay(child=self.microphone)
        microphone_overlay.add_overlay(self.recording_dot)
        self.spinner = Gtk.Spinner(width_request=36, height_request=36)
        self.indicator = Gtk.Stack()
        self.indicator.add_named(microphone_overlay, "icon")
        self.indicator.add_named(self.spinner, "spinner")
        for child in (self.indicator, self.heading):
            line.append(child)
        top.append(line)
        self.instruction = text()
        top.append(self.instruction)
        self.mouse = text()
        top.append(self.mouse)
        self.buttons = Gtk.Box(spacing=8)
        self.record = Gtk.Button(
            label="Start Recording", action_name="app.record", hexpand=True
        )
        self.record.add_css_class("suggested-action")
        self.buttons.append(self.record)
        top.append(self.buttons)
        info = Gtk.Box(spacing=8)
        self.backend = text(hexpand=True)
        self.backend.add_css_class("dim-label")
        info.append(self.backend)
        self.preferences = icon_button(
            "emblem-system-symbolic", "Preferences — backend and microphone", "app.preferences"
        )
        info.append(self.preferences)
        top.append(info)
        self.recovery = Gtk.Button(
            child=text("Back Up Settings and Start Fresh"), action_name="app.recover"
        )
        top.append(self.recovery)
        self.preview = text(selectable=True)
        self.preview.update_property(
            [Gtk.AccessibleProperty.LABEL], ["Current recording preview; not inserted"]
        )
        top.append(self.preview)
        self.review_pending = Gtk.Button(
            label="Review Uninserted Text…",
            action_name="app.transcript",
            halign=Gtk.Align.START,
        )
        top.append(self.review_pending)
        self.inline_panel = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=8, visible=False
        )
        content.append(self.inline_panel)
        inline_header = Gtk.Box(spacing=8)
        self.inline_status = text("Your dictation", hexpand=True)
        self.inline_status.add_css_class("heading")
        inline_header.append(self.inline_status)
        self.inline_hide = Gtk.Button(
            icon_name="pan-up-symbolic", tooltip_text="Hide Dictation Text"
        )
        self.inline_hide.update_property(
            [Gtk.AccessibleProperty.LABEL], ["Hide Dictation Text"]
        )
        self.inline_hide.connect("clicked", lambda *_: self.hide_inline_transcript())
        inline_header.append(self.inline_hide)
        self.inline_panel.append(inline_header)
        self.inline_result = Gtk.TextView(
            editable=False,
            wrap_mode=Gtk.WrapMode.WORD_CHAR,
            top_margin=12,
            bottom_margin=12,
            left_margin=12,
            right_margin=12,
        )
        self.inline_result.update_property(
            [Gtk.AccessibleProperty.LABEL], ["Current button recording transcript"]
        )
        self.inline_scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            min_content_height=180,
            child=self.inline_result,
            vexpand=True,
        )
        self.inline_scroll.add_css_class("card")
        self.inline_panel.append(self.inline_scroll)
        self.inline_last_text = None
        self.inline_previous_height = None
        self.transcript_dialog = Adw.Dialog(
            title="Last Transcript", content_width=600, content_height=420
        )
        self.transcript_dialog.set_size_request(320, 240)
        transcript_toolbar = Adw.ToolbarView()
        transcript_toolbar.add_top_bar(Adw.HeaderBar())
        self.transcript_dialog.set_child(transcript_toolbar)
        bottom = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            margin_start=18,
            margin_end=18,
            margin_top=12,
            margin_bottom=18,
        )
        transcript_toolbar.set_content(
            Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=bottom)
        )
        row = Gtk.Box(spacing=8)
        title = text("Most recent dictation · kept in memory", hexpand=True)
        title.add_css_class("heading")
        row.append(title)
        self.copy = icon_button("edit-copy-symbolic", "Copy Transcript", "app.copy")
        row.append(self.copy)
        self.menu = Gio.Menu()
        self.more = Gtk.MenuButton(
            icon_name="view-more-symbolic",
            tooltip_text="Transcript Actions",
            menu_model=self.menu,
            valign=Gtk.Align.CENTER,
        )
        self.more.update_property([Gtk.AccessibleProperty.LABEL], ["Transcript Actions"])
        row.append(self.more)
        bottom.append(row)
        self.result = Gtk.TextView(
            editable=False,
            wrap_mode=Gtk.WrapMode.WORD_CHAR,
            top_margin=12,
            bottom_margin=12,
            left_margin=12,
            right_margin=12,
        )
        self.result.update_property([Gtk.AccessibleProperty.LABEL], ["Last transcript"])
        self.result_scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER, min_content_height=150, child=self.result
        )
        self.stack = Gtk.Stack(hhomogeneous=False, vhomogeneous=False)
        self.stack.add_css_class("card")
        self.stack.add_named(
            text(
                "Your last transcript will appear here.",
                margin_start=18,
                margin_end=18,
                margin_top=36,
                margin_bottom=36,
            ),
            "empty",
        )
        self.stack.add_named(self.result_scroll, "text")
        bottom.append(self.stack)
        self.footer = Gtk.Box(spacing=12)
        self.paste_button = Gtk.Button(
            label="Paste Again…",
            action_name="app.paste-help",
            halign=Gtk.Align.START,
            valign=Gtk.Align.CENTER,
        )
        self.footer.append(self.paste_button)
        self.paste_hint = text(hexpand=True)
        self.paste_hint.add_css_class("caption")
        self.paste_hint.add_css_class("dim-label")
        self.footer.append(self.paste_hint)
        bottom.append(self.footer)
        self.last_text = None
        self.menu_key = None
        breakpoint = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 480px"))
        for box in (self.buttons,):
            breakpoint.add_setter(box, "orientation", Gtk.Orientation.VERTICAL)
        self.add_breakpoint(breakpoint)
        transcript_breakpoint = Adw.Breakpoint.new(
            Adw.BreakpointCondition.parse("max-width: 480px")
        )
        transcript_breakpoint.add_setter(self.footer, "orientation", Gtk.Orientation.VERTICAL)
        self.transcript_dialog.add_breakpoint(transcript_breakpoint)
        self.connect("close-request", self.close_requested)
        self.connect("notify::is-active", self.focused)

    def show_inline_transcript(self):
        if not self.inline_panel.get_visible():
            self.inline_previous_height = self.get_height()
            self.set_default_size(self.get_width(), max(self.get_height(), 720))
            self.inline_panel.set_visible(True)

    def hide_inline_transcript(self):
        if self.inline_panel.get_visible():
            self.inline_panel.set_visible(False)
            if self.inline_previous_height:
                self.set_default_size(self.get_width(), self.inline_previous_height)
            self.inline_previous_height = None

    def focused(self, *_):
        if self.is_active() and not self.app.session.busy:
            self.app.session.scan_devices()

    def close_requested(self, *_):
        if self.app.tray and self.app.tray.ready:
            self.set_visible(False)
        else:
            self.app.request_quit()
        return True

    def refresh(self):
        c = self.app.session
        p = c.preferences
        state = c.state
        self.heading.set_label(state)
        working = state in ("Loading", "Transcribing")
        self.indicator.set_visible_child_name("spinner" if working else "icon")
        self.spinner.set_spinning(working)
        self.microphone.set_from_icon_name(
            "dialog-warning-symbolic"
            if state == "Setup needed"
            else "audio-input-microphone-symbolic"
        )
        for style in ("dim-label", "warning"):
            self.microphone.remove_css_class(style)
        if state == "Standby":
            self.microphone.add_css_class("dim-label")
        elif state == "Setup needed":
            self.microphone.add_css_class("warning")
        self.recording_dot.set_visible(state == "Listening")
        shortcut = str(Chord.parse(p.shortcut))
        instruction = f"Dictate anywhere: Click a text field in another application and press {shortcut}. Press again or Escape to finish."
        self.instruction.set_text(instruction)
        if c.in_window:
            self.instruction.set_text(
                "Speak naturally. Click Stop Recording or press Escape to finish."
            )
        elif c.pause_dictation:
            self.instruction.set_text(
                f"Pause to insert text. Press {shortcut} or Escape to finish."
            )
        elif c.continuous:
            self.instruction.set_text(
                f"Continuous dictation — press Escape or {Chord.parse(p.continuous_shortcut)} to finish."
            )
        self.mouse.set_text(
            "The finished text is copied to the clipboard when you stop."
            if c.in_window
            else "Agreed words are inserted while you speak. Escape finishes and inserts the remaining text."
            if c.continuous
            and not c.pause_dictation
            and p.backend == "local"
            and p.continuous_live
            else "Completed phrases are inserted at pauses. Escape finishes and inserts the remaining text."
            if c.continuous
            else "Dictate here: Click Start Recording, then Stop Recording when finished. Your transcript is copied to the clipboard."
        )
        labels = {
            "listening": "Stop Recording",
            "starting": "Starting…",
            "loading": "Start When Ready",
            "transcribing": "Transcribing…",
            "delivering": "Waiting for Keys…",
            "cancelling": "Cancelling…",
            "saving": "Saving…",
            "unloading": "Unloading Model…",
            "removing_models": "Removing Models…",
        }
        self.record.set_label(labels.get(c.phase, "Start Recording"))
        if self.app.pending_start and self.app.pending_start[0].startswith("button-"):
            self.record.set_label("Cancel Start")
        if c.continuous and not c.in_window and c.phase == "listening":
            self.record.set_label(
                "Finish Dictation" if c.pause_dictation else "Finish Continuous Dictation"
            )
        if c.capture and c.capture.stop.is_set() and c.phase == "listening":
            self.record.set_label("Finishing…")
        self.backend.set_text(
            f"{'Local · ' + LOCAL_MODELS[p.local_model]['name'] if p.backend == 'local' else 'OpenAI'} · {p.microphone or 'System Default microphone'}"
        )
        self.recovery.set_visible(bool(c.store.problem))
        self.preview.set_text("Preview — not yet inserted: " + c.preview)
        self.preview.set_visible(bool(c.preview))
        if self.inline_panel.get_visible():
            self.inline_hide.set_visible(not c.busy)
            self.inline_status.set_text(
                "Listening — draft text"
                if c.in_window and c.phase == "listening" and p.backend == "local"
                else "Listening — text appears after you stop"
                if c.in_window and c.phase == "listening"
                else "Finishing transcription…"
                if c.in_window and c.busy
                else "Copied to clipboard"
                if c.notice == "Transcript copied."
                else "Your dictation"
            )
            if self.inline_last_text != c.inline_text:
                self.inline_result.get_buffer().set_text(c.inline_text)
                self.inline_last_text = c.inline_text
        self.review_pending.set_visible(bool(c.pending_text))
        has_text = bool(c.result.text)
        self.stack.set_visible_child_name("text" if has_text else "empty")
        self.more.set_sensitive(has_text)
        self.footer.set_visible(has_text)
        self.paste_button.set_label(
            "Paste Uninserted Text…" if c.pending_text else "Paste Again…"
        )
        self.paste_hint.set_text(
            f"Click a text field, then press {Chord.parse(p.paste_shortcut)}."
        )
        if self.last_text != c.result.text:
            self.result.get_buffer().set_text(c.result.text)
            self.last_text = c.result.text
        key = (c.result.can_undo, c.result.undo, bool(c.pending_text))
        if key != self.menu_key:
            self.menu.remove_all()
            self.menu.append("Save Transcript…", "app.save-transcript")
            if c.pending_text:
                self.menu.append("Copy Uninserted Text", "app.copy-pending")
            if c.result.can_undo:
                self.menu.append(
                    "Restore Transcript Changes"
                    if c.result.undo
                    else "Undo Transcript Changes",
                    "app.undo",
                )
            self.menu_key = key
