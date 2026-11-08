# SPDX-License-Identifier: GPL-3.0-only
# ruff: noqa: E402
"""Independent Preferences tabs and optional voice setup on a private desktop.

Downloads, synthesis and output are fixtures; no user microphone or speaker opens.
"""

import os
from pathlib import Path
import threading
from unittest.mock import patch

assert os.environ["HOME"].startswith("/tmp/dictadesk-test.")
from dictadesk.audio.speech import SpeechOutput
from dictadesk.core.commands import registry
from dictadesk.ui.application import Application
from dictadesk.ui.gtk import GLib
from test_tts import Worker


class Store:
    downloaded = False

    def root(self):
        return Path(os.environ["HOME"]) / "tts-fixture"

    def runtime(self):
        return self.root() / "runtime"

    def installed(self, _path):
        return self.downloaded

    def ready(self, engine, model):
        return engine == "espeak" or self.downloaded

    def install(self, model, cancel, progress):
        install_started.set()
        while not install_release.wait(0.01):
            if cancel.is_set():
                raise InterruptedError()
        self.downloaded = True
        progress(100, 100)

    def note_use(self):
        pass


app = Application(isolated=True)
app.model_prompts_shown.add(("parakeet", "int8"))
errors, spoken = [], []
store, worker = Store(), Worker()
install_started, install_release = threading.Event(), threading.Event()
output_started, output_active = threading.Event(), threading.Event()


def output(rate, data, cancel):
    assert rate == 16000 and data
    output_active.set()
    output_started.set()
    try:
        if not cancel.wait(5):
            raise AssertionError("Preview was not cancelled")
        raise InterruptedError()
    finally:
        output_active.clear()


def make_speech(dispatch, changed):
    return SpeechOutput(dispatch, changed, store=store, worker=worker, output=output)


def later(fn, delay=250):
    def call():
        try:
            fn()
        except Exception as exc:
            import traceback

            traceback.print_exc()
            errors.append(exc)
            app.request_quit()
        return False

    GLib.timeout_add(delay, call)


def capture(page):
    prefix = os.environ.get("DICTADESK_CAPTURE_PREFS")
    if prefix:
        import subprocess
        from gi.repository import GdkX11

        xid = GdkX11.X11Surface.get_xid(app.preferences_dialog.dialog.get_surface())
        subprocess.run(["import", "-window", str(xid), f"{prefix}-{page}.png"], check=True)


def start():
    assert not app.get_windows()
    assert not worker.requests and not app.speech.downloading
    app.session.recognizer = Worker()
    app.session.recognizer.loaded = True
    app.session.microphone_problem = ""
    app.show_preferences()
    p = app.preferences_dialog
    assert p.stack.get_visible_child_name() == "dictation"
    assert tuple(p.pages) == ("dictation", "commands", "tts")
    assert p.controls["hands_free"].is_ancestor(p.pages["commands"])
    assert p.controls["volume_step"].is_ancestor(p.pages["commands"])
    assert p.speech_settings.enabled.is_ancestor(p.pages["tts"])
    assert p.speech_settings.banner.is_ancestor(p.pages["tts"])
    for banner in (p.voice_banner, p.speech_settings.banner):
        assert banner.get_label() == "Experimental"
        assert banner.has_css_class("experimental-banner")
    later(dictation)


def dictation():
    p = app.preferences_dialog
    monitor = p.dialog.get_display().get_monitor_at_surface(p.dialog.get_surface())
    assert p.dialog.get_width() <= monitor.get_geometry().width - 64
    assert p.dialog.get_height() <= monitor.get_geometry().height - 96
    capture("dictation")
    app.show_preferences("commands")
    assert app.preferences_dialog is p
    assert p.stack.get_visible_child_name() == "commands"
    later(commands)


def commands():
    capture("commands")
    app.show_preferences("tts")
    later(download)


def download():
    p = app.preferences_dialog
    s = p.speech_settings
    assert p.stack.get_visible_child_name() == "tts"
    assert s.banner.get_mapped()
    assert not s.enabled.get_active() and not s.enabled.get_sensitive()
    assert s.phrase.get_text() == "Hello"
    assert s.download.get_sensitive() and not s.preview.get_sensitive()
    capture("talkback")
    s.download.emit("clicked")
    later(downloading)


def downloading():
    assert install_started.is_set()
    assert app.speech.downloading and not app.session.busy
    assert app.session.can_record, "Optional download blocked dictation"
    install_release.set()
    later(preview)


def preview():
    assert not app.speech.downloading
    s = app.preferences_dialog.speech_settings
    s.refresh()
    assert s.enabled.get_sensitive() and s.preview.get_sensitive()
    s.phrase.set_text("Ready for you")
    s.enabled.set_active(True)
    s.preview.emit("clicked")
    later(cancel_preview)


def cancel_preview():
    assert output_started.is_set() and output_active.is_set()
    assert app.session.preferences.tts_phrase == "Hello", "Preview saved draft settings"
    assert worker.requests[-1]["text"] == "Ready for you"
    # Exercise the same operation hook used by manual dictation: it cancels output
    # before the capture worker may start, even while Preferences is open.
    ticket = app.session._begin("loading")
    app.session._run(
        ticket,
        lambda *_: (
            not output_active.is_set()
            or (_ for _ in ()).throw(AssertionError("Operation overlapped preview"))
        ),
        lambda *_: None,
    )
    later(save)


def save():
    assert not app.session.busy and not app.speech.previewing
    assert not output_active.is_set() and not app.session.last_dictation
    p = app.preferences_dialog
    s = p.speech_settings
    s.engine.set_selected(s.engine_values.index("espeak"))
    s.model.set_selected(s.model_values.index("en-gb"))

    def saved(prefs, key, done):
        assert prefs.tts_enabled and prefs.tts_phrase == "Ready for you"
        assert (prefs.tts_engine, prefs.tts_model) == ("espeak", "en-gb")
        app.session.preferences = prefs
        done()

    with patch.object(app, "save_preferences", side_effect=saved) as write:
        p.save_clicked()
        write.assert_called_once()
    assert app.preferences_dialog is None
    app.show_preferences()
    p = app.preferences_dialog
    assert p.stack.get_visible_child_name() == "dictation"
    assert p.speech_settings.values()["tts_phrase"] == "Ready for you"
    p.speech_settings.phrase.set_text("Discard me")
    p.dialog.close()
    assert app.session.preferences.tts_phrase == "Ready for you"
    for action, page in (
        ("usage", "dictation"),
        ("command-help", "commands"),
        ("tts-help", "tts"),
    ):
        app.activate_action(action, None)
        assert app.usage_window.stack.get_visible_child_name() == page
    assert "Ready for you" in app.usage_window.tts.get_text()
    app.usage_window.dialog.close()
    assert not app.get_windows()
    print(
        "PASS Preferences tabs / Talk Back: default Dictation, independent sections, optional nonblocking download, unsaved preview, operation cancels output, Save/Cancel, separate dynamic help"
    )
    app.request_quit()


with (
    patch("dictadesk.core.session.check"),
    patch("dictadesk.core.session.microphones", return_value=[]),
    patch("dictadesk.ui.preferences.read_key", return_value=""),
    patch(
        "dictadesk.ui.preferences.compute_capabilities",
        return_value={"cuda": False, "reason": "fixture"},
    ),
    patch("dictadesk.ui.application.SpeechOutput", side_effect=make_speech),
    patch(
        "dictadesk.desktop.commands.DesktopCommands.probe",
        return_value={s.capability: "" for s in registry()},
    ),
):
    later(start)
    app.run(["dictadesk-talkback-test"])
assert not errors, errors
