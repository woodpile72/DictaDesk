# SPDX-License-Identifier: GPL-3.0-only
from .gtk import Adw, Gtk
from .widgets import text


class MicrophoneDialog:
    def __init__(self, app):
        self.app = app
        self.dialog = Adw.Dialog(title="Test Microphone", content_width=440, content_height=400)
        toolbar = Adw.ToolbarView()
        self.dialog.set_child(toolbar)
        toolbar.add_top_bar(Adw.HeaderBar())
        content = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=14,
            margin_start=18,
            margin_end=18,
            margin_top=18,
            margin_bottom=18,
        )
        toolbar.set_content(
            Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=content)
        )
        content.append(
            text(
                "Record up to eight seconds using your saved settings. The sample stays here and is never pasted."
            )
        )
        self.level = Gtk.LevelBar(min_value=0, max_value=1)
        self.level.update_property([Gtk.AccessibleProperty.LABEL], ["Microphone input level"])
        content.append(self.level)
        self.status = text()
        content.append(self.status)
        self.start = Gtk.Button(label="Record Sample")
        self.start.add_css_class("suggested-action")
        self.start.connect("clicked", lambda *_: app.session.record(sample=True))
        content.append(self.start)
        self.cancel = Gtk.Button(label="Cancel Sample", action_name="app.cancel")
        content.append(self.cancel)
        self.result = text(selectable=True)
        content.append(self.result)
        self.dialog.connect("closed", self.closed)

    def closed(self, *_):
        if self.app.session.busy:
            self.app.session.cancel()
        self.app.microphone_dialog = None

    def refresh(self):
        c = self.app.session
        self.start.set_sensitive(c.can_record or c.phase == "listening")
        self.start.set_label("Finish Sample" if c.phase == "listening" else "Record Sample")
        self.cancel.set_visible(c.busy)
        self.level.set_value(min(1, (c.capture.level if c.capture else c.sample_peak) * 4))
        self.status.set_text(c.notice or c.microphone_problem or c.state)
        self.result.set_text(c.sample_text)

    def present(self):
        self.refresh()
        self.dialog.present(self.app.dialog_parent())
