# SPDX-License-Identifier: GPL-3.0-only
"""Optional speech controls on the independent Speech preferences tab."""

from dataclasses import replace
import shutil
import time

from ..core.tts import ENGINES, VOICES, espeak_available
from .gtk import Adw, Gio, Gtk
from .widgets import experimental_banner


class SpeechPreferences:
    def __init__(self, app):
        self.app = app
        prefs = app.session.preferences
        self.banner_group = Adw.PreferencesGroup()
        self.banner = experimental_banner()
        self.banner_group.add(self.banner)
        self.setup_group = Adw.PreferencesGroup(title="Setup Required")
        self.installing = False
        self.setting_up = False
        self.group = Adw.PreferencesGroup(
            title="Speech — Experimental",
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
        self.setup_group.add(self.status)
        self.download = Gtk.Button(label="Download Engine & Voice", valign=Gtk.Align.CENTER)
        self.download.connect(
            "clicked",
            lambda *_: app.speech.download(self.model_id()) if not app.session.busy else None,
        )
        self.status.add_suffix(self.download)
        self.cancel = Gtk.Button(label="Cancel Download", valign=Gtk.Align.CENTER)
        self.cancel.connect("clicked", lambda *_: app.speech.download_cancel.set())
        self.status.add_suffix(self.cancel)
        self.install = Gtk.Button(label="Install eSpeak NG…", valign=Gtk.Align.CENTER)
        self.install.add_css_class("suggested-action")
        self.install.connect("clicked", self.install_espeak)
        self.status.add_suffix(self.install)
        self.recheck = Gtk.Button(label="Check Again", valign=Gtk.Align.CENTER)
        self.recheck.connect("clicked", self.check_installation)
        self.status.add_suffix(self.recheck)
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
            label="Speech Help…", action_name="app.tts-help", halign=Gtk.Align.START
        )
        self.group.add(self.help)
        self.engine.connect("notify::selected", self.engine_changed)
        self.model.connect("notify::selected", lambda *_: self.refresh())
        self.enabled.connect("notify::active", self.enabled_changed)
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
        espeak_available.cache_clear()
        self.app.speech.problem = self.app.speech.notice = ""
        self.set_models()
        self.refresh()
        if (
            not self.setting_up
            and self.enabled.get_active()
            and not self.app.speech.store.ready(self.engine_id(), self.model_id())
        ):
            self.enabled.set_active(False)
            self.app.preferences_dialog.open_feature_setup("tts")

    def enabled_changed(self, *_):
        if (
            not self.setting_up
            and self.enabled.get_active()
            and not self.app.speech.store.ready(self.engine_id(), self.model_id())
        ):
            self.enabled.set_active(False)
            self.app.preferences_dialog.open_feature_setup("tts")
        self.refresh()

    def check_installation(self, *_):
        espeak_available.cache_clear()
        if espeak_available():
            self.app.speech.problem = ""
            self.app.speech.retry_at = 0
        self.app.refresh()
        self.refresh()

    def install_espeak(self, *_):
        if self.installing or self.app.session.busy:
            return
        installer = shutil.which("apturl")
        if not installer:
            self.app.speech.problem = "Install espeak-ng using your system's Software Manager, then choose Check Again. This desktop has no APT URL installer."
            self.refresh()
            return
        try:
            # The desktop installer owns its confirmation/authentication UI.
            # Fixed package URI only; DictaDesk itself never runs as root.
            process = Gio.Subprocess.new([installer, "apt:espeak-ng"], Gio.SubprocessFlags.NONE)
            self.installing = True

            def finished(process, result):
                try:
                    process.wait_finish(result)
                except Exception:
                    self.app.speech.problem = "The installer did not finish. Install espeak-ng using Software Manager, then choose Check Again."
                finally:
                    self.installing = False
                    if not self.app.shutting_down:
                        self.check_installation()

            process.wait_async(None, finished)
        except Exception:
            self.installing = False
            self.app.speech.problem = "Could not open the system installer. Install espeak-ng using Software Manager, then choose Check Again."
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
        self.setup_group.set_title("Voice Ready" if ready else "Setup Required")
        self.status.set_title(
            VOICES[model].name
            if ready
            else "Install eSpeak NG to use this voice"
            if engine == "espeak"
            else "Download engine and voice to enable Speech"
        )
        self.enabled.set_sensitive(not c.busy)
        self.engine.set_sensitive(not busy)
        self.model.set_sensitive(not busy)
        self.download.set_visible(engine == "piper" and not speech.downloading)
        self.download.set_sensitive(not ready and not busy and not c.busy and not cache_problem)
        self.download.set_label("Downloaded" if ready else "Download")
        self.download.add_css_class("suggested-action")
        self.download.set_tooltip_text(
            "Download the selected voice"
            if runtime_installed
            else "Download the optional engine and selected voice"
        )
        self.cancel.set_visible(speech.downloading)
        self.install.set_visible(engine == "espeak" and not ready)
        self.install.set_sensitive(not self.installing and not c.busy)
        self.recheck.set_visible(engine == "espeak" and not ready)
        self.recheck.set_sensitive(not c.busy)
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
        elif not ready and engine == "espeak":
            message = "This system voice requires the espeak-ng package. Choose Install eSpeak NG and confirm in the system installer, then Check Again. No restart is needed. You can also switch back to Piper."
        elif not ready:
            message = f"Speech cannot speak until {VOICES[model].name} is downloaded. {VOICES[model].description} Choose Download here; ordinary dictation remains available."
        elif speech.notice:
            message = speech.notice
        else:
            message = "Voice ready. Test uses the choices here; Save applies them to wake acknowledgment."
        self.status.set_subtitle(message)
