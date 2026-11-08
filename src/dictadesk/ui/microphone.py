# SPDX-License-Identifier: GPL-3.0-only
from dataclasses import replace

from ..core.models import downloaded, LOCAL_MODELS
from .gtk import Adw, Gtk
from .widgets import text


class MicrophoneDialog:
    def __init__(self, app):
        self.app = app
        self.sample_operation = None
        self.preferences = replace(app.session.preferences)
        self.api_key = ""
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
                "Record up to eight seconds using the options selected in Preferences, including unsaved changes. The sample is never copied or pasted."
            )
        )
        self.selection = text()
        content.append(self.selection)
        self.level = Gtk.LevelBar(min_value=0, max_value=1)
        self.level.update_property([Gtk.AccessibleProperty.LABEL], ["Microphone input level"])
        content.append(self.level)
        self.status = text()
        content.append(self.status)
        self.start = Gtk.Button(label="Record Sample")
        self.start.add_css_class("suggested-action")
        self.start.connect("clicked", self.start_clicked)
        content.append(self.start)
        self.cancel = Gtk.Button(label="Cancel Sample", action_name="app.cancel")
        content.append(self.cancel)
        self.result = text(selectable=True)
        content.append(self.result)
        self.dialog.connect("closed", self.closed)

    def closed(self, *_):
        c = self.app.session
        if c.busy and c.operation == self.sample_operation:
            c.cancel()
        self.api_key = ""
        self.app.microphone_dialog = None

    def model_missing(self):
        p = self.preferences
        return p.backend == "local" and not downloaded(p.quantization, p.local_model)

    def start_clicked(self, *_):
        c = self.app.session
        if c.busy:
            if c.phase == "listening" and c.operation == self.sample_operation:
                c.finish_recording()
            return
        if self.model_missing():
            self.app.session.download_model(
                self.preferences.quantization, model=self.preferences.local_model
            )
            return
        before = c.operation
        c.record(sample=True, preferences=self.preferences, api_key=self.api_key)
        if c.operation != before:
            self.sample_operation = c.operation
        self.refresh()

    def refresh(self):
        c = self.app.session
        own = c.operation == self.sample_operation
        can_test = c.can_sample
        self.start.set_sensitive(can_test or (own and c.phase == "listening"))
        self.start.set_label(
            "Finish Sample"
            if own and c.phase == "listening"
            else "Loading Model…"
            if c.phase == "loading"
            else "Finishing Sample…"
            if c.busy
            else "Download Model"
            if self.model_missing()
            else "Record Sample"
        )
        self.cancel.set_visible(c.busy)
        self.cancel.set_label("Cancel Sample" if own else "Cancel Download")
        self.level.set_value(min(1, (c.capture.level if c.capture else c.sample_peak) * 4))
        self.status.set_text(
            c.notice or c.state
            if c.busy or own
            else "Download the selected model before recording a sample."
            if self.model_missing()
            else "Ready. The selected model loads automatically when needed."
        )
        self.result.set_text(c.sample_text if own else "")

    def present(self):
        if self.app.preferences_dialog:
            self.preferences, self.api_key = self.app.preferences_dialog.sample_settings()
        else:
            self.preferences = replace(self.app.session.preferences)
        p = self.preferences
        backend = (
            LOCAL_MODELS[p.local_model]["name"]
            if p.backend == "local"
            else "Remote transcription"
        )
        self.selection.set_text(f"{p.microphone or 'System Default microphone'} · {backend}")
        self.refresh()
        self.dialog.present(self.app.dialog_parent())
