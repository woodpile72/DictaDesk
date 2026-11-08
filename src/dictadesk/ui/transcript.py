# SPDX-License-Identifier: GPL-3.0-only
"""On-demand transcript recovery window; no recording controls."""

from ..desktop.keyboard import Chord
from .gtk import Adw, Gio, Gtk
from .widgets import text, icon_button


class TranscriptWindow:
    def __init__(self, app):
        self.app = app
        self.dialog = Adw.Window(
            application=app, title="Last Transcript", default_width=600, default_height=420
        )
        self.dialog.set_size_request(320, 240)
        transcript_toolbar = Adw.ToolbarView()
        transcript_toolbar.add_top_bar(Adw.HeaderBar())
        self.dialog.set_content(transcript_toolbar)
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
        self.dialog.connect("close-request", self.closed)

    def closed(self, *_):
        self.app.transcript_window = None
        return False

    def present(self):
        self.refresh()
        self.dialog.present()

    def refresh(self):
        c = self.app.session
        p = c.preferences
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
