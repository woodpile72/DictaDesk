# SPDX-License-Identifier: GPL-3.0-only
# ruff: noqa: E402
"""Preferences surface, child dialogs and close behavior on different displays."""

import os
from unittest.mock import patch

assert os.environ["HOME"].startswith("/tmp/dictadesk-test.")
from dictadesk.ui.application import Application
from dictadesk.ui.gtk import Adw, Gtk, GLib
from gi.repository import GdkX11
from Xlib import X, XK, display
from Xlib.ext import xtest

Adw.init()
Gtk.Settings.get_default().set_property("gtk-enable-animations", False)
app = Application(isolated=True)
failures = []
original_size = None


def later(fn):
    def call():
        try:
            fn()
        except Exception as exc:
            import traceback

            traceback.print_exc()
            failures.append(exc)
            app.request_quit()
        return False

    GLib.timeout_add(400, call)


def start():
    global original_size
    original_size = (app.window.get_width(), app.window.get_height())
    app.show_preferences()
    later(check_window)


def check_window():
    p = app.preferences_dialog
    monitor = p.dialog.get_display().get_monitor_at_surface(p.dialog.get_surface())
    geometry = monitor.get_geometry()
    assert p.dialog.get_width() <= geometry.width - 64
    assert p.dialog.get_height() <= geometry.height - 96
    if geometry.height >= 900:
        assert p.dialog.get_height() >= 800
    assert (app.window.get_width(), app.window.get_height()) == original_size
    app.show_preferences()
    assert app.preferences_dialog is p, "Repeated activation opened another window"
    later(check_child)


children = iter(
    [
        (app.show_microphone_test, None),
        (app.remove_models, "Remove Downloaded Models?"),
        (lambda: app.confirm_download("int8"), "Download the Model Again?"),
        (
            lambda: app.preferences_dialog.capture_shortcut("record_shortcut"),
            "Press a Shortcut",
        ),
        (lambda: app.preferences_dialog.edit_rule(), "Vocabulary Entry"),
    ]
)


def check_child():
    item = next(children, None)
    if item is None:
        edit_preferences()
        return
    action, heading = item
    action()

    def close_child():
        p = app.preferences_dialog
        child = p.dialog.get_visible_dialog()
        assert child is not None and child.get_root() is p.dialog
        if heading:
            assert child.get_heading() == heading
        child.close()
        later(check_child)

    later(close_child)


def edit_preferences():
    p = app.preferences_dialog
    p.controls["backend"][0].set_selected(1)
    p.api_key.set_text("unsaved-fixture")
    p.controls["sentence_pause_seconds"].set_value(2)
    p.controls["silence_stop_seconds"].set_value(0)
    assert p.controls["silence_stop_seconds"].get_text() == "Off"
    later(escape)


def escape():
    p = app.preferences_dialog
    p.save.grab_focus()
    client = display.Display()
    client.set_input_focus(
        GdkX11.X11Surface.get_xid(p.dialog.get_surface()), X.RevertToParent, X.CurrentTime
    )
    key = client.keysym_to_keycode(XK.string_to_keysym("Escape"))
    xtest.fake_input(client, X.KeyPress, key)
    xtest.fake_input(client, X.KeyRelease, key)
    client.sync()
    client.close()
    later(after_escape)


def after_escape():
    assert app.preferences_dialog is None
    assert app.session.preferences.backend == "local"
    assert (app.window.get_width(), app.window.get_height()) == original_size
    app.show_preferences()
    p = app.preferences_dialog
    assert p.controls["backend"][0].get_selected() == 0
    assert p.controls["sentence_pause_seconds"].get_value() == 4
    assert p.controls["silence_stop_seconds"].get_value() == 20
    p.controls["sentence_pause_seconds"].set_value(2)
    p.controls["silence_stop_seconds"].set_value(0)
    assert p.controls["silence_stop_seconds"].get_text() == "Off"
    p.controls["silence_stop_seconds"].set_value(30)
    assert p.controls["silence_stop_seconds"].get_text() == "30"
    p.controls["silence_stop_seconds"].set_text("0")
    p.controls["silence_stop_seconds"].update()
    assert p.controls["silence_stop_seconds"].get_value() == 0
    assert p.controls["silence_stop_seconds"].get_text() == "Off"

    # Exercise Save through its existing completion callback without touching
    # login startup or credentials in this presentation-only check.
    def save(prefs, key, close):
        assert prefs.backend == "local" and not key
        assert prefs.sentence_pause_seconds == 2 and prefs.silence_stop_seconds == 0
        close()

    with patch.object(app, "save_preferences", side_effect=save) as saved:
        p.save_clicked()
        saved.assert_called_once()
    assert app.preferences_dialog is None
    print(
        "PASS Preferences: display bounds, unchanged parent, nested dialogs, Escape discards, Save closes"
    )
    app.request_quit()


with (
    patch("dictadesk.core.session.check"),
    patch("dictadesk.core.session.microphones", return_value=[]),
    patch("dictadesk.ui.preferences.read_key", return_value=""),
    patch(
        "dictadesk.ui.preferences.compute_capabilities",
        return_value={"cuda": False, "reason": "CPU test"},
    ),
):
    later(start)
    app.run(["dictadesk-preferences-test"])
assert not failures, failures
