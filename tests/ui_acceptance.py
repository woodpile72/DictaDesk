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
    c, w = app.session, app.window
    assert c.state == "Setup needed"
    assert w.microphone.get_icon_name() == "dialog-warning-symbolic"
    assert w.microphone.has_css_class("warning")
    assert app.lookup_action("record").get_enabled()
    assert not hasattr(w, "load") and not hasattr(w, "repair")
    assert w.transcript_dialog.get_root() is None
    assert not w.inline_panel.get_visible()
    shot("setup")
    c.microphone_problem = ""
    installed = patch("dictadesk.core.session.downloaded", return_value=True)
    installed.start()
    app.refresh()
    assert c.state == "Standby" and w.heading.get_label() == "Standby"
    assert w.microphone.get_icon_name() == "audio-input-microphone-symbolic"
    assert w.microphone.has_css_class("dim-label")
    assert app.lookup_action("record").get_enabled()

    def standby():
        try:
            shot("standby")
        finally:
            installed.stop()
        loading()

    later(standby)


def loading():
    c, w = app.session, app.window
    c.phase = "loading"
    c.notice = ""
    app.refresh()
    assert w.heading.get_label() == "Loading"
    assert w.spinner.get_spinning() and not hasattr(w, "cancel")
    assert w.indicator.get_visible_child_name() == "spinner"
    later(ready)


def ready():
    c, w = app.session, app.window
    c.phase = "idle"
    c.microphone_problem = ""
    c.recognizer.loaded = True
    app.refresh()
    assert c.state == "Ready" and app.lookup_action("record").get_enabled()
    assert w.indicator.get_visible_child_name() == "icon"
    assert not w.microphone.has_css_class("dim-label")
    assert not w.recording_dot.get_visible()
    assert not hasattr(w, "load")
    assert w.instruction.get_next_sibling() is w.mouse
    assert w.mouse.get_next_sibling() is w.buttons
    assert not hasattr(w, "detail")
    assert w.instruction.get_text().startswith("Dictate anywhere:")
    assert w.mouse.get_text().startswith("Dictate here:")
    c.notice = "No speech was recognized."
    app.refresh()
    assert w.mouse.get_next_sibling() is w.buttons
    c.notice = ""
    previous_shortcut = c.preferences.shortcut
    c.preferences.shortcut = "<Super>m"
    app.refresh()
    assert "Super+M" in w.instruction.get_text() and "Super+V" not in w.instruction.get_text()
    c.preferences.shortcut = previous_shortcut
    app.refresh()
    c.result.replace("This is dictadesk speaking.", [["dictadesk", "DictaDesk"]])
    app.refresh()
    assert w.stack.get_visible_child_name() == "text"
    assert w.menu.get_n_items() == 2
    later(result)


def result():
    shot("main-window")
    app.activate_action("transcript", None)
    later(transcript)


def transcript():
    assert app.window.transcript_dialog.get_mapped()
    c = app.session
    shot("last-transcript")
    app.activate_action("undo", None)
    assert c.result.text == "This is dictadesk speaking."
    app.activate_action("undo", None)
    assert "DictaDesk" in c.result.text
    app.activate_action("copy", None)
    assert c.notice == "Transcript copied."
    app.window.transcript_dialog.close()
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
    assert p.dialog.get_resizable() and p.dialog.get_transient_for() is app.window
    assert p.dialog.get_height() >= 800
    assert app.window.get_height() < 500, "Preferences resized its parent"
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
    c = app.session
    c.notice = ""
    c.phase = "listening"
    app.refresh()
    assert app.window.record.get_label() == "Stop Recording"
    assert not hasattr(app.window, "cancel")
    later(listening)


def listening():
    assert app.window.recording_dot.get_visible()
    assert not app.window.spinner.get_spinning()
    shot("listening")
    app.window.show_inline_transcript()
    app.session.in_window = True
    app.session.inline_text = "This is the draft appearing while I speak."
    app.refresh()
    assert app.window.inline_panel.get_visible()
    assert app.window.record.get_label() == "Stop Recording"
    later(inline_listening)


def inline_listening():
    shot("button-dictation")
    assert app.window.get_height() >= 700
    app.session.in_window = False
    app.session.phase = "idle"
    app.session.notice = "Transcript copied."
    app.refresh()
    assert app.window.inline_status.get_text() == "Copied to clipboard"
    assert app.window.inline_panel.get_visible()
    later(inline_complete)


def inline_complete():
    shot("button-dictation-complete")
    app.window.hide_inline_transcript()
    assert not app.window.inline_panel.get_visible()
    app.session.phase = "listening"
    app.session.continuous = True
    app.session.preview = "the ending can still change while I speak"
    app.session.result.replace("This text has already been inserted.", [])
    app.refresh()
    assert "Escape" in app.window.instruction.get_text()
    assert app.window.preview.get_visible()
    later(live_preview)


def live_preview():
    shot("live-preview")
    app.session.continuous = False
    app.session.preview = ""
    app.session.phase = "transcribing"
    app.refresh()
    assert app.window.heading.get_label() == "Transcribing"
    assert not app.lookup_action("record").get_enabled()
    later(processing)


def processing():
    shot("transcribing")
    app.session.phase = "cancelling"
    app.refresh()
    assert not app.lookup_action("cancel").get_enabled()
    assert app.session.result.text
    app.session.phase = "idle"
    app.session.notice = "Cancelled. Nothing was inserted."
    app.window.set_default_size(360, 740)
    Gtk.Settings.get_default().set_property("gtk-font-name", "Sans 18")
    app.refresh()
    later(narrow)


def narrow():
    w = app.window
    assert w.get_width() <= 370, w.get_width()
    assert w.content.measure(Gtk.Orientation.HORIZONTAL, -1)[0] <= w.get_width()
    w.record.grab_focus()
    assert w.get_focus()
    shot("narrow-large-text")
    app.show_preferences()
    app.preferences_dialog.dialog.set_default_size(360, 740)
    later(narrow_preferences)


def narrow_preferences():
    p = app.preferences_dialog
    assert p.dialog.get_width() <= app.window.get_width()
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
    shot("about")
    print(
        "PASS GTK: setup, model loading, readiness, transcript, vocabulary undo, Preferences Cancel, recording, processing, cancellation, narrow/large text, About"
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
