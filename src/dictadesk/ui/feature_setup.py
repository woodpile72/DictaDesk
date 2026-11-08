# SPDX-License-Identifier: GPL-3.0-only
"""Guided feature setup, reusing the existing model and speech services."""

from ..core.models import LOCAL_MODELS, downloaded
from ..core.tts import ENGINES, VOICES
from .gtk import Adw, Gtk


class FeatureSetup:
    def __init__(self, preferences, kind):
        self.preferences, self.app, self.kind = preferences, preferences.app, kind
        self.speech = preferences.speech_settings
        self.closed = False
        self.own_download = None
        self.step = 0
        title = "Set Up Command & Control" if kind == "commands" else "Set Up Talk Back"
        self.dialog = Adw.Dialog(title=title, content_width=560, content_height=520)
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self.dialog.close())
        header.pack_start(cancel)
        self.back = Gtk.Button(label="Back")
        self.back.connect("clicked", lambda *_: self.show_step(self.step - 1))
        header.pack_start(self.back)
        self.next = Gtk.Button(label="Next")
        self.next.add_css_class("suggested-action")
        self.next.connect("clicked", lambda *_: self.show_step(self.step + 1))
        header.pack_end(self.next)
        self.use = Gtk.Button(label="Enable")
        self.use.add_css_class("suggested-action")
        self.use.connect("clicked", self.enable)
        header.pack_end(self.use)
        toolbar.add_top_bar(header)
        self.dialog.set_child(toolbar)
        self.stack = Gtk.Stack()
        toolbar.set_content(self.stack)
        pages = []
        for index in range(3):
            page = Adw.PreferencesPage()
            self.stack.add_named(page, str(index))
            pages.append(page)
        choices = Adw.PreferencesGroup(
            title="1. Choose an Engine",
            description="Choose the engine and model, install the required files, then Enable. Save Preferences to apply your choices. Cancel leaves the feature off.",
        )
        pages[0].add(choices)
        self.engines = ("local",) if kind == "commands" else tuple(ENGINES)
        self.engine = Adw.ComboRow(
            title="Engine",
            model=Gtk.StringList.new(
                ["Local speech recognition"] if kind == "commands" else list(ENGINES.values())
            ),
            selected=0 if kind == "commands" else self.engines.index(self.speech.engine_id()),
        )
        choices.add(self.engine)
        models = Adw.PreferencesGroup(title="2. Choose a Model / Voice")
        pages[1].add(models)
        self.model = Adw.ComboRow(
            title="Model" if kind == "commands" else "Voice", use_subtitle=True
        )
        models.add(self.model)
        self.precision = Adw.ComboRow(title="Precision")
        models.add(self.precision)
        self.about = Adw.ActionRow(title="About this choice", use_markup=False)
        models.add(self.about)
        self.install_group = Adw.PreferencesGroup(title="3. Download or Install Required Files")
        pages[2].add(self.install_group)
        self.status = Adw.ActionRow(use_markup=False)
        self.install_group.add(self.status)
        self.download = Gtk.Button(label="Download", valign=Gtk.Align.CENTER)
        self.download.add_css_class("suggested-action")
        self.download.connect("clicked", self.download_files)
        self.status.add_suffix(self.download)
        self.recheck = Gtk.Button(label="Check Again", valign=Gtk.Align.CENTER)
        self.recheck.connect("clicked", lambda *_: self.speech.check_installation())
        self.status.add_suffix(self.recheck)
        self.stop_download = Gtk.Button(label="Cancel Download", valign=Gtk.Align.CENTER)
        self.stop_download.connect("clicked", self.cancel_download)
        self.install_group.add(self.stop_download)
        self.populate()
        self.engine.connect("notify::selected", lambda *_: self.populate())
        self.model.connect("notify::selected", lambda *_: self.model_changed())
        self.precision.connect("notify::selected", lambda *_: self.refresh())
        self.dialog.connect("closed", self.close)
        self.refresh()

    def engine_id(self):
        return self.engines[self.engine.get_selected()]

    def show_step(self, step):
        if not self.busy():
            self.step = min(2, max(0, step))
            self.stack.set_visible_child_name(str(self.step))
            self.refresh()

    def model_id(self):
        index = self.model.get_selected()
        return self.models[index] if index < len(self.models) else self.models[0]

    def precision_id(self):
        index = self.precision.get_selected()
        return self.precisions[index] if index < len(self.precisions) else self.precisions[0]

    def populate(self):
        self.models = (
            tuple(LOCAL_MODELS)
            if self.kind == "commands"
            else tuple(key for key, voice in VOICES.items() if voice.engine == self.engine_id())
        )
        selected = (
            self.preferences.selected_model()
            if self.kind == "commands"
            else self.speech.model_id()
        )
        self.model.set_model(
            Gtk.StringList.new(
                [
                    LOCAL_MODELS[key]["name"] if self.kind == "commands" else VOICES[key].name
                    for key in self.models
                ]
            )
        )
        self.model.set_selected(self.models.index(selected) if selected in self.models else 0)
        self.model_changed()

    def model_changed(self):
        self.precisions = (
            tuple(LOCAL_MODELS[self.model_id()]["sizes"])
            if self.kind == "commands"
            else ("int8",)
        )
        self.precision.set_model(Gtk.StringList.new(list(self.precisions)))
        self.precision.set_visible(self.kind == "commands" and len(self.precisions) > 1)
        selected = self.preferences.selected_precision()
        self.precision.set_selected(
            self.precisions.index(selected) if selected in self.precisions else 0
        )
        self.refresh()

    def ready(self):
        return (
            downloaded(self.precision_id(), self.model_id())
            if self.kind == "commands"
            else self.app.speech.store.ready(self.engine_id(), self.model_id())
        )

    def busy(self):
        s = self.app.speech
        return (
            self.app.session.busy
            or s.downloading
            or s.removing
            or s.preparing
            or s.previewing
            or s.speaking
            or self.speech.installing
        )

    def refresh(self):
        if self.closed:
            return
        ready, busy = self.ready(), self.busy()
        c, s = self.app.session, self.app.speech
        system_voice = self.kind == "tts" and self.engine_id() == "espeak"
        self.back.set_visible(self.step > 0)
        self.back.set_sensitive(not busy)
        self.next.set_visible(self.step < 2)
        self.next.set_sensitive(not busy)
        self.use.set_visible(self.step == 2)
        self.use.set_sensitive(ready and not busy)
        self.engine.set_sensitive(not busy)
        self.model.set_sensitive(not busy)
        self.precision.set_sensitive(not busy and len(self.precisions) > 1)
        self.download.set_label(
            "Installed" if ready else "Install eSpeak NG…" if system_voice else "Download"
        )
        self.download.set_sensitive(not ready and not busy)
        self.recheck.set_visible(system_voice and not ready)
        self.recheck.set_sensitive(not busy)
        self.stop_download.set_visible(
            bool(self.own_download) and (c.busy if self.kind == "commands" else s.downloading)
        )
        if self.kind == "commands":
            spec = LOCAL_MODELS[self.model_id()]
            name = spec["name"]
            self.about.set_subtitle(spec["description"])
            detail = f"Download approximately {spec['sizes'][self.precision_id()]}. Shared with dictation; Local transcription is selected when you enable this feature."
            problem = c.problem[2] if c.problem else ""
        else:
            name = VOICES[self.model_id()].name
            self.about.set_subtitle(VOICES[self.model_id()].description)
            detail = (
                "Uses your system's espeak-ng package. Confirm installation in the desktop installer; then Check Again."
                if system_voice
                else VOICES[self.model_id()].description
            )
            problem = s.problem
        if busy:
            done, total = s.progress
            message = (
                f"Downloading: {done / 1000000:.1f} of {total / 1000000:.1f} MB…"
                if self.kind == "tts" and s.downloading and total
                else "Working… Wait for setup to finish."
            )
        elif ready:
            message = "Ready. Enable this feature, then Save Preferences."
        else:
            message = detail + ("\n" + problem if problem else "")
        self.status.set_title(
            "Ready"
            if ready and not busy
            else "Setup in progress"
            if busy
            else name + " — Required files are missing"
        )
        self.status.set_subtitle(message)

    def download_files(self, *_):
        if self.busy() or self.ready():
            return
        if self.kind == "commands":
            self.app.session.download_model(self.precision_id(), model=self.model_id())
            self.own_download = self.app.session.operation
        elif self.engine_id() == "espeak":
            self.speech.install_espeak()
        else:
            self.own_download = True
            self.app.speech.download(self.model_id())
        self.refresh()

    def cancel_download(self, *_):
        if self.kind == "commands":
            if self.own_download == self.app.session.operation:
                self.app.session.cancel()
        elif self.own_download:
            self.app.speech.download_cancel.set()

    def enable(self, *_):
        if not self.ready() or self.busy():
            return
        p = self.preferences
        p.setting_up = self.speech.setting_up = True
        try:
            if self.kind == "commands":
                for name, value in (
                    ("backend", "local"),
                    ("local_model", self.model_id()),
                    ("quantization", self.precision_id()),
                ):
                    row, values = p.controls[name]
                    row.set_selected(values.index(value))
                p.controls["hands_free"].set_active(True)
            else:
                self.speech.engine.set_selected(
                    self.speech.engine_values.index(self.engine_id())
                )
                self.speech.model.set_selected(self.speech.model_values.index(self.model_id()))
                self.speech.enabled.set_active(True)
        finally:
            p.setting_up = self.speech.setting_up = False
        self.dialog.close()
        p.refresh()

    def close(self, *_):
        self.closed = True
        if self.preferences.feature_setup is self:
            self.preferences.feature_setup = None

    def present(self):
        self.dialog.present(self.preferences.dialog)
