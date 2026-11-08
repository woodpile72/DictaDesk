# SPDX-License-Identifier: GPL-3.0-only
# ruff: noqa: E402
"""Real GTK widgets with simulated speech; never records or calls a paid service."""

import os
from pathlib import Path
import subprocess
from unittest.mock import patch

assert os.environ["HOME"].startswith("/tmp/dictadesk-test.")
from dictadesk.ui.application import Application
from dictadesk.ui.gtk import Adw, Gtk, GLib
from gi.repository import GdkX11

Adw.init()
Gtk.Settings.get_default().set_property("gtk-enable-animations", False)
Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
app = Application(isolated=True)
app.model_prompts_shown.add(("parakeet", "int8"))
failures = []
out = Path(__file__).resolve().parents[1] / ".qa"
if os.environ.get("ADW_DEBUG_HIGH_CONTRAST") == "1":
    out = out / "high-contrast"
out.mkdir(parents=True, exist_ok=True)


def later(fn):
    def call():
        try:
            fn()
        except Exception as exc:
            import traceback

            traceback.print_exc()
            failures.append(exc)
            app.session.phase = "idle"
            app.request_quit()
        return False

    GLib.timeout_add(500, call)


def shot(name):
    xid = GdkX11.X11Surface.get_xid(app.dialog_parent().get_surface())
    subprocess.run(["import", "-window", str(xid), str(out / f"{name}.png")], check=True)


def start():
    assert not app.get_windows()
    app.session.microphone_problem = ""
    app.session.recognizer.loaded = True
    app.show_preferences()
    later(preferences)


def preferences():
    row, values = app.preferences_dialog.controls["device"]
    assert not row.get_sensitive() and values[row.get_selected()] == "cpu"
    assert "CPU runtime" in app.preferences_dialog.compute_status.get_subtitle()
    p = app.preferences_dialog
    assert "mode" not in p.controls
    assert p.controls["sentence_pause_seconds"].get_value() == 4.0
    assert "thinking pauses" in p.controls["sentence_pause_seconds"].get_subtitle()
    assert "without ending the sentence" in p.controls["pause_seconds"].get_subtitle()
    assert p.dialog.get_visible()
    assert p.dialog.get_resizable() and p.dialog.get_transient_for() is None
    assert p.dialog.get_height() >= 800
    assert not hasattr(app, "window")
    assert p.local_group.get_visible() and not p.remote_group.get_visible()
    assert p.download_button.get_sensitive()
    assert "640 MiB" in p.download_row.get_subtitle()
    p.controls["quantization"][0].set_selected(1)
    assert "2.4 GiB" in p.download_row.get_subtitle()
    p.controls["quantization"][0].set_selected(0)
    shot("preferences-local")
    assert p.controls["local_model"][1] == (
        "parakeet",
        "moonshine-tiny",
        "whisper-base",
        "whisper-tiny",
    )
    p.controls["local_model"][0].set_selected(2)
    assert "104 MiB" in p.download_row.get_subtitle()
    assert "Whisper Base" in p.selected_remove_row.get_subtitle()
    assert app.session.preferences.local_model == "parakeet", (
        "Unsaved model selection changed active backend"
    )
    shot("preferences-whisper")
    p.controls["local_model"][0].set_selected(3)
    assert "40 MiB" in p.download_row.get_subtitle()
    assert "omit or repeat" in p.model_description.get_subtitle()
    p.controls["quantization"][0].set_selected(1)
    assert "146 MiB" in p.download_row.get_subtitle()
    p.controls["local_model"][0].set_selected(1)
    assert "42 MiB" in p.download_row.get_subtitle()
    assert p.selected_precision() == "int8"
    assert not p.controls["quantization"][0].get_sensitive()
    assert not p.controls["device"][0].get_sensitive()
    assert "English only" in p.model_description.get_subtitle()
    p.show_model_guide()
    later(model_guide)


def model_guide():
    p = app.preferences_dialog
    assert p.model_guide.get_visible()
    assert p.model_guide.get_focus() == p.model_guide.close_button
    assert app.session.preferences.local_model == "parakeet"
    shot("model-guide")
    from Xlib import X, XK, display
    from Xlib.ext import xtest

    client = display.Display()
    client.set_input_focus(
        GdkX11.X11Surface.get_xid(p.dialog.get_surface()), X.RevertToParent, X.CurrentTime
    )
    escape = client.keysym_to_keycode(XK.string_to_keysym("Escape"))
    xtest.fake_input(client, X.KeyPress, escape)
    xtest.fake_input(client, X.KeyRelease, escape)
    client.sync()
    client.close()
    later(after_model_guide)


def after_model_guide():
    p = app.preferences_dialog
    assert not p.model_guide.get_mapped()
    assert p.dialog.get_visible()
    assert p.selected_model() == "moonshine-tiny"
    p.controls["local_model"][0].set_selected(0)
    assert p.controls["quantization"][0].get_sensitive()
    p.controls["backend"][0].set_selected(1)
    assert not p.local_group.get_visible() and p.remote_group.get_visible()
    assert p.values["cloud_model"] == "gpt-transcribe"
    assert p.remote_model.get_subtitle() == "GPT Transcribe"
    assert p.remote_model.get_sensitive()
    assert not p.download_button.get_sensitive()
    p.api_key.set_text("fixture-key")
    p.check_connection()
    later(remote_preferences)


def remote_preferences():
    p = app.preferences_dialog
    assert "No audio was sent" in p.connection_row.get_subtitle()
    assert app.session.preferences.backend == "local"  # Test does not save the choice.
    p.api_key.set_text("changed-key")
    assert "Does not save changes" in p.connection_row.get_subtitle()
    p.storage_info = {"exists": True, "bytes": 640 * 1024**2, "unused_days": 31}
    p.show_storage()
    assert p.cleanup_offer.get_visible()
    assert "31 days" in p.cleanup_offer.get_subtitle()
    shot("preferences-remote")
    p.storage_info["unused_days"] = 0
    p.show_storage()
    assert not p.cleanup_offer.get_visible()
    p.replace_rules([["code x", "Codex"]])
    p.remove_rule(0)
    p.undo_rule()
    assert p.rules == [["code x", "Codex"]]
    p.dialog.close()
    assert not app.session.preferences.vocabulary
    app.show_preferences()
    app.preferences_dialog.dialog.set_default_size(360, 740)
    Gtk.Settings.get_default().set_property("gtk-font-name", "Sans 18")
    later(narrow_preferences)


def narrow_preferences():
    p = app.preferences_dialog
    assert p.dialog.get_width() <= 370
    assert (
        p.dialog.get_content().measure(Gtk.Orientation.HORIZONTAL, -1)[0]
        <= p.dialog.get_width()
    )
    shot("preferences-large-text")
    p.show_model_guide()
    later(narrow_guide)


def narrow_guide():
    p = app.preferences_dialog
    guide = p.model_guide
    assert guide.get_width() <= p.dialog.get_width()
    assert guide.get_child().measure(Gtk.Orientation.HORIZONTAL, -1)[0] <= guide.get_width()
    shot("model-guide-large-text")
    guide.close()
    p.controls["backend"][0].set_selected(1)
    later(narrow_remote)


def narrow_remote():
    p = app.preferences_dialog
    assert (
        p.dialog.get_content().measure(Gtk.Orientation.HORIZONTAL, -1)[0]
        <= p.dialog.get_width()
    )
    shot("preferences-remote-large-text")
    p.dialog.close()
    app.show_about()
    later(done)


def done():
    assert any(isinstance(w, Adw.AboutWindow) for w in app.get_windows())
    print(
        "PASS GTK: tray-only startup, Preferences/model controls, vocabulary undo, Cancel, narrow/large text, About"
    )
    app.request_quit()


with (
    patch("dictadesk.core.session.check"),
    patch("dictadesk.core.session.microphones", return_value=[]),
    patch("dictadesk.ui.preferences.read_key", return_value=""),
    patch(
        "dictadesk.ui.preferences.compute_capabilities",
        return_value={"cuda": False, "reason": "CPU runtime only."},
    ),
    patch(
        "dictadesk.ui.preferences.test_connection",
        return_value="Connected. No audio was sent.",
    ),
):
    later(start)
    app.run(["dictadesk-test"])
assert not failures, failures
