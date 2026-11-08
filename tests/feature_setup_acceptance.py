# SPDX-License-Identifier: GPL-3.0-only
# ruff: noqa: E402
"""Guided enable/download/install/cancel flows; no real installer or audio device."""

import os
from pathlib import Path
from unittest.mock import Mock, patch

assert os.environ["HOME"].startswith("/tmp/dictadesk-test.")
from dictadesk.audio.speech import SpeechOutput
from dictadesk.ui.application import Application
from dictadesk.ui.gtk import Gtk, GLib
from test_tts import Worker

Gtk.Settings.get_default().set_property("gtk-enable-animations", False)
app = Application(isolated=True)
errors, models, voices, callbacks = [], set(), set(), []
system_voice = [False]
cache = Path(os.environ["HOME"]) / "tts-fixture"
store = Mock()
store.root.return_value = cache
store.runtime.return_value = cache / "runtime"
store.installed.return_value = False
store.ready.side_effect = lambda engine, model: (
    system_voice[0] if engine == "espeak" else model in voices
)
store.remove.side_effect = voices.clear
store.install.side_effect = lambda model, cancel, progress: voices.add(model)
process = Mock()
process.wait_async.side_effect = lambda cancellable, callback: callbacks.append(callback)


def make_speech(dispatch, changed):
    return SpeechOutput(dispatch, changed, store=store, worker=Worker(), output=lambda *_: None)


def later(fn):
    def run():
        try:
            fn()
        except Exception as exc:
            import traceback

            traceback.print_exc()
            errors.append(exc)
            app.request_quit()
        return False

    GLib.timeout_add(300, run)


def start():
    app.session.microphone_problem = ""
    app.session.recognizer = Worker()
    app.session.recognizer.loaded = True
    app.show_preferences("commands")
    p = app.preferences_dialog
    assert p.command_setup.get_visible()
    assert p.dialog.get_visible_dialog() is None, "Unrelated dictation prompt interrupted setup"
    p.controls["backend"][0].set_selected(1)
    p.controls["hands_free"].set_active(True)
    later(command_setup)


def command_setup():
    p = app.preferences_dialog
    w = p.feature_setup
    assert w.kind == "commands" and not w.use.get_sensitive()
    assert not p.controls["hands_free"].get_active()
    capture("commands")
    w.next.emit("clicked")
    assert w.step == 1
    w.model.set_selected(w.models.index("moonshine-tiny"))
    assert w.precision_id() == "int8"
    w.next.emit("clicked")
    assert w.step == 2
    later(command_download)


def command_download():
    w = app.preferences_dialog.feature_setup
    capture("commands-download")

    def download(precision, *, model):
        assert (precision, model) == ("int8", "moonshine-tiny")
        models.add((precision, model))

    with patch.object(app.session, "download_model", side_effect=download) as request:
        w.download.emit("clicked")
        request.assert_called_once()
    w.refresh()
    assert w.use.get_sensitive()
    w.use.emit("clicked")
    later(command_enabled)


def command_enabled():
    p = app.preferences_dialog
    assert p.feature_setup is None
    assert p.controls["hands_free"].get_active()
    assert p.controls["backend"][0].get_selected() == 0
    assert p.selected_model() == "moonshine-tiny"
    assert not app.session.preferences.hands_free, "Setup bypassed Save"
    app.show_preferences("tts")
    s = p.speech_settings
    assert s.setup_group.get_title() == "Setup Required"
    s.enabled.set_active(True)
    later(talkback_setup)


def talkback_setup():
    p = app.preferences_dialog
    w = p.feature_setup
    assert w.kind == "tts" and not p.speech_settings.enabled.get_active()
    capture("talkback")
    w.engine.set_selected(w.engines.index("espeak"))
    assert w.model_id() == "en-us"
    w.next.emit("clicked")
    w.next.emit("clicked")
    assert w.step == 2
    later(espeak_install)


def espeak_install():
    w = app.preferences_dialog.feature_setup
    capture("espeak-install")
    assert w.download.get_label() == "Install eSpeak NG…"
    assert not w.use.get_sensitive()
    with (
        patch("dictadesk.ui.speech_preferences.shutil.which", return_value="/fixture/apturl"),
        patch(
            "dictadesk.ui.speech_preferences.Gio.Subprocess.new", return_value=process
        ) as spawn,
    ):
        w.download.emit("clicked")
        assert spawn.call_args.args[0] == ["/fixture/apturl", "apt:espeak-ng"]
    assert not app.session.busy, "System installer blocked recording"
    assert not w.use.get_sensitive()
    # A cancelled install must leave a usable retry path, without enabling.
    callbacks.pop()(process, object())
    w.refresh()
    assert not w.use.get_sensitive() and w.download.get_sensitive()
    system_voice[0] = True
    with patch("dictadesk.ui.speech_preferences.espeak_available", return_value=True):
        w.recheck.emit("clicked")
    w.refresh()
    assert w.use.get_sensitive()
    w.use.emit("clicked")
    later(talkback_enabled)


def talkback_enabled():
    p = app.preferences_dialog
    s = p.speech_settings
    assert p.feature_setup is None and s.enabled.get_active()
    assert s.engine_id() == "espeak"
    # Removing optional downloads must leave the installed system engine usable.
    voices.add("en_US-lessac-medium")
    cache.mkdir(mode=0o700)
    s.refresh()
    s.remove.emit("clicked")
    later(after_removal)


def after_removal():
    p = app.preferences_dialog
    s = p.speech_settings
    assert not app.speech.removing and not voices
    s.refresh()
    assert s.preview.get_sensitive() and s.engine.get_sensitive()
    s.engine.set_selected(s.engine_values.index("piper"))
    later(cancel_piper)


def cancel_piper():
    p = app.preferences_dialog
    w = p.feature_setup
    assert w.kind == "tts" and not p.speech_settings.enabled.get_active()
    assert not w.use.get_sensitive() and w.download.get_sensitive()
    w.dialog.close()
    later(retry)


def retry():
    p = app.preferences_dialog
    assert p.feature_setup is None and not p.speech_settings.enabled.get_active()
    assert p.speech_settings.download.get_sensitive()
    p.speech_settings.enabled.set_active(True)
    later(download_piper)


def download_piper():
    w = app.preferences_dialog.feature_setup
    w.next.emit("clicked")
    w.next.emit("clicked")
    w.download.emit("clicked")
    later(finish)


def finish():
    p = app.preferences_dialog
    w = p.feature_setup
    assert w.ready() and w.use.get_sensitive()
    assert app.session.can_record
    w.use.emit("clicked")
    later(done)


def done():
    p = app.preferences_dialog
    assert p.feature_setup is None and p.speech_settings.enabled.get_active()
    assert p.speech_settings.engine_id() == "piper"
    assert not app.session.preferences.tts_enabled
    p.dialog.close()
    print(
        "PASS guided setup: missing-model enable gate, independent model choice/download, local engine adoption, cancelled eSpeak install/recheck, system voice survives removal, switch/cancel/retry/Piper download, Save required, recording available"
    )
    app.request_quit()


def capture(name):
    target = os.environ.get("DICTADESK_CAPTURE_SETUP")
    if target:
        import subprocess
        from gi.repository import GdkX11

        xid = GdkX11.X11Surface.get_xid(app.preferences_dialog.dialog.get_surface())
        subprocess.run(
            ["import", "-window", str(xid), target + "-" + name + ".png"], check=True
        )


with (
    patch("dictadesk.core.session.check"),
    patch("dictadesk.core.session.microphones", return_value=[]),
    patch("dictadesk.ui.preferences.read_key", return_value=""),
    patch(
        "dictadesk.ui.preferences.compute_capabilities",
        return_value={"cuda": False, "reason": "fixture"},
    ),
    patch(
        "dictadesk.ui.preferences.downloaded",
        side_effect=lambda precision, model: (precision, model) in models,
    ),
    patch(
        "dictadesk.ui.feature_setup.downloaded",
        side_effect=lambda precision, model: (precision, model) in models,
    ),
    patch("dictadesk.ui.application.SpeechOutput", side_effect=make_speech),
):
    later(start)
    app.run(["dictadesk-feature-setup-test"])
assert not errors, errors
