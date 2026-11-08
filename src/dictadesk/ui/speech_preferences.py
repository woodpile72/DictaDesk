# SPDX-License-Identifier: GPL-3.0-only
"""Optional speech controls on the independent Talk Back preferences tab."""

from dataclasses import replace
import time

from ..core.tts import ENGINES, VOICES
from .gtk import Adw, Gtk


class SpeechPreferences:
    def __init__(self, app):
        self.app = app
        prefs = app.session.preferences
        self.group = Adw.PreferencesGroup(
            title="Talk Back — Experimental",
            description="Optional spoken wake acknowledgment. Off by default. Voices are downloaded only when you choose Download; speech stays on this computer.",
        )
        self.enabled = Adw.SwitchRow(
            title="Enable spoken acknowledgment", active=prefs.tts_enabled
        )
        self.group.add(self.enabled)
        self.engine_values = tuple(ENGINES)
        self.engine = Adw.ComboRow(
            title="Speech engine",
            model=Gtk.StringList.new(list(ENGINES.values())),
            selected=self.engine_values.index(prefs.tts_engine),
        )
        self.group.add(self.engine)
        self.model = Adw.ComboRow(title="Voice / model", use_subtitle=True)
        self.group.add(self.model)
        self.model_values = ()
        self.set_models(prefs.tts_model)
        self.phrase = Adw.EntryRow(title="Spoken acknowledgment", text=prefs.tts_phrase)
        self.group.add(self.phrase)
        self.description = Adw.ActionRow(title="About this voice", use_markup=False)
        self.group.add(self.description)
        self.status = Adw.ActionRow(title="Voice setup", use_markup=False)
        self.group.add(self.status)
        self.download = Gtk.Button(label="Download Engine & Voice", valign=Gtk.Align.CENTER)
        self.download.connect(
            "clicked",
            lambda *_: app.speech.download(self.model_id()) if not app.session.busy else None,
        )
        self.status.add_suffix(self.download)
        self.cancel = Gtk.Button(label="Cancel Download", valign=Gtk.Align.CENTER)
        self.cancel.connect("clicked", lambda *_: app.speech.download_cancel.set())
        self.status.add_suffix(self.cancel)
        buttons = Gtk.FlowBox(
            selection_mode=Gtk.SelectionMode.NONE,
            column_spacing=8,
            row_spacing=6,
            min_children_per_line=1,
            max_children_per_line=3,
        )
        self.preview = Gtk.Button(label="Test Voice")
        self.preview.connect("clicked", self.test_voice)
        buttons.insert(self.preview, -1)
        self.stop = Gtk.Button(label="Stop Preview")
        self.stop.connect("clicked", lambda *_: app.speech.stop_playback())
        buttons.insert(self.stop, -1)
        self.remove = Gtk.Button(label="Remove TTS Downloads")
        self.remove.connect(
            "clicked", lambda *_: app.speech.remove() if not app.session.busy else None
        )
        buttons.insert(self.remove, -1)
        self.group.add(buttons)
        self.help = Gtk.Button(
            label="Talk Back Help…", action_name="app.tts-help", halign=Gtk.Align.START
        )
        self.group.add(self.help)
        self.engine.connect("notify::selected", self.engine_changed)
        self.model.connect("notify::selected", lambda *_: self.refresh())
        self.enabled.connect("notify::active", lambda *_: self.refresh())
        self.refresh()

    def engine_id(self):
        return self.engine_values[self.engine.get_selected()]

    def model_id(self):
        index = self.model.get_selected()
        return (
            self.model_values[index] if index < len(self.model_values) else self.model_values[0]
        )

    def set_models(self, selected=None):
        self.model_values = tuple(
            key for key, voice in VOICES.items() if voice.engine == self.engine_id()
        )
        self.model.set_model(
            Gtk.StringList.new([VOICES[key].name for key in self.model_values])
        )
        self.model.set_selected(
            self.model_values.index(selected) if selected in self.model_values else 0
        )

    def engine_changed(self, *_):
        self.set_models()
        self.refresh()

    def values(self):
        return {
            "tts_enabled": self.enabled.get_active(),
            "tts_engine": self.engine_id(),
            "tts_model": self.model_id(),
            "tts_phrase": self.phrase.get_text().strip(),
        }

    def test_voice(self, *_):
        c, speech = self.app.session, self.app.speech
        if c.busy or any(not check.is_set() for check in c.device_checks):
            return
        try:
            prefs = replace(c.preferences, **self.values()).validate()
        except ValueError as exc:
            speech.problem = str(exc)
            self.refresh()
            return

        def finished():
            self.app.handsfree.resume_at = time.monotonic() + 1

        speech.preview(
            prefs,
            suspend=self.app.handsfree.suspend,
            join=self.app.handsfree.join,
            finished=finished,
        )

    def refresh(self):
        speech, c = self.app.speech, self.app.session
        engine, model = self.engine_id(), self.model_id()
        ready = speech.store.ready(engine, model)
        busy = (
            speech.downloading
            or speech.previewing
            or speech.preparing
            or speech.removing
            or speech.speaking
        )
        cache_problem = ""
        try:
            runtime_installed = speech.store.installed(speech.store.runtime())
            downloads_exist = speech.store.root().exists()
        except (OSError, ValueError) as exc:
            runtime_installed, downloads_exist = False, False
            cache_problem = str(exc)
        self.description.set_subtitle(VOICES[model].description)
        self.enabled.set_sensitive(ready or self.enabled.get_active())
        self.engine.set_sensitive(not busy)
        self.model.set_sensitive(not busy)
        self.download.set_visible(engine == "piper" and not speech.downloading)
        self.download.set_sensitive(not ready and not busy and not c.busy and not cache_problem)
        self.download.set_label("Downloaded" if ready else "Download")
        self.download.set_tooltip_text(
            "Download the selected voice"
            if runtime_installed
            else "Download the optional engine and selected voice"
        )
        self.cancel.set_visible(speech.downloading)
        self.preview.set_sensitive(
            ready
            and not busy
            and not c.busy
            and all(check.is_set() for check in c.device_checks)
        )
        self.stop.set_visible(speech.previewing)
        self.remove.set_sensitive(not busy and not c.busy and downloads_exist)
        if speech.downloading:
            done, total = speech.progress
            message = (
                f"Downloading: {done / 1000000:.1f} of {total / 1000000:.1f} MB…"
                if total
                else "Preparing optional download…"
            )
        elif speech.removing:
            message = "Removing optional speech downloads…"
        elif speech.previewing:
            message = "Playing the voice preview. Dictation or Stop Preview interrupts it."
        elif speech.preparing:
            message = "Preparing the local speech voice…"
        elif cache_problem:
            message = cache_problem
        elif speech.problem:
            message = speech.problem
        elif speech.notice:
            message = speech.notice
        elif ready:
            message = "Voice ready. Test uses the choices here; Save applies them to wake acknowledgment."
        elif engine == "espeak":
            message = "System eSpeak NG is not installed. Install espeak-ng from Software Manager and restart DictaDesk, or choose Piper and download a voice."
        else:
            message = "Not downloaded. Download the engine and selected voice before enabling spoken acknowledgment. Dictation does not need these files."
        self.status.set_subtitle(message)
