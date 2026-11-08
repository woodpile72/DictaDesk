# SPDX-License-Identifier: GPL-3.0-only
# ruff: noqa: E402
"""Real input tests for help dismissal, setup prompts and repeat microphone samples."""

import os
import time
from unittest.mock import patch

assert os.environ["HOME"].startswith("/tmp/dictadesk-test.")
from Xlib import X, XK, display
from Xlib.ext import xtest
from dictadesk.ui.application import Application
from dictadesk.ui.gtk import Adw, Gtk, GLib
from gi.repository import GdkX11
from test_samples import Worker, SampleCapture

Adw.init()
Gtk.Settings.get_default().set_property("gtk-enable-animations", False)
app = Application(isolated=True)
app.model_prompts_shown.add(("parakeet", "int8"))
errors = []
x = display.Display()


def later(callback, delay=200):
    def invoke():
        try:
            callback()
        except Exception as exc:
            import traceback

            traceback.print_exc()
            errors.append(exc)
            app.request_quit()
        return False

    GLib.timeout_add(delay, invoke)


def until(predicate, callback):
    deadline = time.monotonic() + 8

    def check():
        assert time.monotonic() < deadline, "Operation timed out"
        if predicate():
            callback()
        else:
            later(check, 25)

    later(check, 25)


def walk(widget):
    yield widget
    child = widget.get_first_child()
    while child:
        yield from walk(child)
        child = child.get_next_sibling()


def button(root, label):
    return next(w for w in walk(root) if isinstance(w, Gtk.Button) and w.get_label() == label)


def focus(window):
    xid = GdkX11.X11Surface.get_xid(window.get_surface())
    x.set_input_focus(xid, X.RevertToParent, X.CurrentTime)
    x.sync()
    return xid


def click(widget):
    window = widget.get_root()
    xid = focus(window)
    success, rect = widget.compute_bounds(window)
    assert success and rect.get_width() > 0
    point = x.screen().root.translate_coords(
        x.create_resource_object("window", xid),
        int(rect.get_x() + rect.get_width() / 2),
        int(rect.get_y() + rect.get_height() / 2),
    )
    xtest.fake_input(x, X.MotionNotify, x=point.x, y=point.y)
    xtest.fake_input(x, X.ButtonPress, 1)
    xtest.fake_input(x, X.ButtonRelease, 1)
    x.sync()


def start():
    app.session.microphone_problem = ""
    app.session.recognizer.loaded = True
    app.show_preferences()
    later(preferences)


def preferences():
    p = app.preferences_dialog
    assert not p.dialog.get_modal(), "Preferences blocks other application windows"
    parent = p.controls["hands_free"].get_parent()
    rows = list(walk(parent))
    assert rows.index(p.controls["hands_free"]) < rows.index(p.wake_phrase)
    assert rows.index(p.controls["command_sound"]) < rows.index(p.controls["volume_step"])
    assert rows.index(p.voice_status) < rows.index(p.controls["volume_step"])
    assert rows.index(p.controls["volume_step"]) < rows.index(p.controls["brightness_step"])
    click(button(p.dialog, "Dictation Help…"))
    later(close_button)


def close_button():
    assert app.usage_window and app.preferences_dialog.dialog.get_visible()
    click(app.usage_window.close_button)
    until(lambda: app.usage_window is None, reopen_for_escape)


def reopen_for_escape():
    click(button(app.preferences_dialog.dialog, "Dictation Help…"))
    later(escape)


def escape():
    focus(app.usage_window.dialog)
    key = x.keysym_to_keycode(XK.string_to_keysym("Escape"))
    xtest.fake_input(x, X.KeyPress, key)
    xtest.fake_input(x, X.KeyRelease, key)
    x.sync()
    until(lambda: app.usage_window is None, reopen_for_title_close)


def reopen_for_title_close():
    click(button(app.preferences_dialog.dialog, "Dictation Help…"))
    later(title_close)


def title_close():
    help = app.usage_window
    close = next(
        w for w in walk(help.dialog) if isinstance(w, Gtk.Button) and w.has_css_class("close")
    )
    click(close)
    until(lambda: app.usage_window is None, first_sample)


def first_sample():
    p = app.preferences_dialog
    p.input.set_selected(1)
    p.controls["local_model"][0].set_selected(2)
    app.show_microphone_test()
    later(record_first)


def record_first():
    assert app.microphone_dialog.start.get_sensitive()
    click(app.microphone_dialog.start)
    until(lambda: not app.session.busy and app.session.sample_text, first_sample_done)


def first_sample_done():
    assert SampleCapture.seen[-1].microphone == "First input"
    assert SampleCapture.seen[-1].local_model == "whisper-base"
    assert app.session.preferences.microphone == ""
    assert app.session.preferences.local_model == "parakeet"
    app.microphone_dialog.dialog.close()
    until(lambda: app.microphone_dialog is None, second_sample)


def second_sample():
    p = app.preferences_dialog
    p.input.set_selected(2)
    p.controls["local_model"][0].set_selected(3)
    app.show_microphone_test()
    later(record_second)


def record_second():
    assert app.microphone_dialog.start.get_sensitive(), (
        "Sample button stayed disabled after changing options"
    )
    click(app.microphone_dialog.start)
    until(
        lambda: not app.session.busy and SampleCapture.seen[-1].local_model == "whisper-tiny",
        second_sample_done,
    )


def second_sample_done():
    assert SampleCapture.seen[-1].microphone == "Second input"
    assert app.microphone_dialog.start.get_sensitive()
    app.microphone_dialog.dialog.close()
    until(lambda: app.microphone_dialog is None, unloaded_sample)


def unloaded_sample():
    app.session.recognizer.loaded = False
    app.session.microphone_problem = "The saved input is missing"
    app.preferences_dialog.controls["local_model"][0].set_selected(0)
    app.show_microphone_test()
    later(record_unloaded)


def record_unloaded():
    assert app.microphone_dialog.start.get_sensitive()
    click(app.microphone_dialog.start)
    until(lambda: not app.session.busy and app.session.recognizer.loaded, unloaded_done)


def unloaded_done():
    assert app.session.sample_text == "sample speech"
    app.microphone_dialog.dialog.close()
    until(lambda: app.microphone_dialog is None, missing_model)


def missing_model():
    app.preferences_dialog.dialog.close()
    app.model_prompts_shown.clear()
    installed.return_value = False
    mic_installed.return_value = False
    app.session.recognizer.loaded = False
    app.show_preferences()
    later(model_prompt)


def model_prompt():
    p = app.preferences_dialog
    assert p.setup_group.get_visible()
    assert p.model_prompt and p.model_prompt.get_heading() == "Download a Speech Model?"
    download.assert_not_called()
    click(button(p.model_prompt, "Not Now"))
    until(lambda: p.model_prompt is None, prompt_dismissed)


def prompt_dismissed():
    download.assert_not_called()
    p = app.preferences_dialog
    p.dialog.close()
    app.show_preferences()
    later(not_repeated)


def not_repeated():
    p = app.preferences_dialog
    assert p.model_prompt is None, "Setup prompt repeated after Not Now"
    assert p.setup_group.get_visible()
    app.show_microphone_test()
    later(missing_sample_model)


def missing_sample_model():
    microphone = app.microphone_dialog
    assert microphone.start.get_sensitive()
    assert microphone.start.get_label() == "Download Model"
    click(microphone.start)
    later(missing_sample_downloaded)


def missing_sample_downloaded():
    download.assert_called_once_with("int8", model="parakeet")
    download.reset_mock()
    app.microphone_dialog.dialog.close()
    until(lambda: app.microphone_dialog is None, top_download)


def top_download():
    click(app.preferences_dialog.setup_download)
    later(done)


def done():
    download.assert_called_once_with("int8", model="parakeet")
    app.preferences_dialog.dialog.close()
    assert not app.get_windows()
    print(
        "PASS real UI input: Close/title close/Escape with Preferences open; adjustment order; unsaved input/model sample retries and automatic loading; prominent first-run model prompt and explicit download"
    )
    app.request_quit()


with (
    patch("dictadesk.core.session.check"),
    patch(
        "dictadesk.core.session.microphones",
        return_value=[(1, "First input", "ALSA"), (2, "Second input", "ALSA")],
    ),
    patch("dictadesk.core.session.Recognizer", Worker),
    patch("dictadesk.core.session.Capture", SampleCapture),
    patch("dictadesk.core.session.downloaded", return_value=True),
    patch("dictadesk.ui.preferences.downloaded", return_value=True) as installed,
    patch("dictadesk.ui.microphone.downloaded", return_value=True) as mic_installed,
    patch(
        "dictadesk.ui.preferences.compute_capabilities",
        return_value={"cuda": False, "reason": "CPU fixture"},
    ),
    patch("dictadesk.desktop.commands.DesktopCommands.probe", return_value={}),
    patch("dictadesk.core.session.Session.download_model") as download,
):
    later(start)
    app.run(["dictadesk-ux-test"])
x.close()
assert not errors, errors
