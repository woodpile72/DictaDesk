# SPDX-License-Identifier: GPL-3.0-only
# ruff: noqa: E402
"""Registry-driven help and independent Preferences on a private GTK desktop."""

import os
from unittest.mock import patch

assert os.environ["HOME"].startswith("/tmp/dictadesk-test.")
from dictadesk.core.commands import registry, CATEGORIES
from dictadesk.ui.application import Application
from dictadesk.ui.gtk import Gtk, GLib

app = Application(isolated=True)
app.model_prompts_shown.add(("parakeet", "int8"))
failures = []


def later(callback):
    def run():
        try:
            callback()
        except Exception as exc:
            import traceback

            traceback.print_exc()
            failures.append(exc)
            app.request_quit()
        return False

    GLib.timeout_add(450, run)


def start():
    assert not app.get_windows()
    p = app.session.preferences
    p.wake_phrase = "Hello DictaDesk"
    p.shortcut = "<Super>m"
    p.continuous_shortcut = "<Super><Shift>m"
    p.volume_step = 7
    p.brightness_step = 9
    app.activate_action("usage", None)
    later(check)


def check():
    help = app.usage_window
    assert help.dialog.get_mapped()
    assert help.stack.get_visible_child_name() == "dictation"
    if os.environ.get("DICTADESK_CAPTURE_HELP"):
        import subprocess
        from gi.repository import GdkX11

        xid = GdkX11.X11Surface.get_xid(help.dialog.get_surface())
        subprocess.run(
            ["import", "-window", str(xid), os.environ["DICTADESK_CAPTURE_HELP"]], check=True
        )
    assert not hasattr(app, "window")
    assert help.row_ids == [
        s.ident for category in CATEGORIES for s in registry() if s.category == category
    ]
    assert "Hello DictaDesk" in help.wake.get_text()
    assert "Super+M" in help.standard.get_text()
    assert "Super+Shift+M" in help.extended.get_text()
    assert "system commands" in help.extended.get_text()
    assert set(help.tables) == set(CATEGORIES)
    app.show_usage("commands")
    assert app.usage_window is help
    assert help.stack.get_visible_child_name() == "commands"
    assert "One phrase" in help.wake.get_text()
    app.show_usage("tts")
    assert app.usage_window is help
    assert help.stack.get_visible_child_name() == "tts"
    assert "Hello" in help.tts.get_text()
    app.show_usage()
    app.show_preferences()
    later(preferences)


def preferences():
    assert app.preferences_dialog.dialog.get_mapped()
    assert app.usage_window.dialog.get_mapped()
    assert app.preferences_dialog.controls["volume_step"].get_value() == 7
    assert app.preferences_dialog.controls["brightness_step"].get_value() == 9
    app.preferences_dialog.dialog.close()
    app.usage_window.dialog.set_default_size(360, 500)
    Gtk.Settings.get_default().set_property("gtk-font-name", "Sans 16")
    later(narrow)


def narrow():
    help = app.usage_window
    assert help.dialog.get_width() <= 370, help.dialog.get_width()
    assert help.content.measure(Gtk.Orientation.HORIZONTAL, -1)[0] <= help.dialog.get_width()
    help.dialog.close()
    assert app.usage_window is None and not app.get_windows()
    app.show_usage()
    later(done)


def done():
    assert app.usage_window.dialog.get_mapped()
    app.usage_window.dialog.close()
    assert not app.get_windows()
    print(
        "PASS Usage & Commands: registry/category consistency, configured shortcuts/wake word/increments, single instance, independent Preferences, narrow large-text layout and tray-only close"
    )
    app.request_quit()


with (
    patch("dictadesk.core.session.check"),
    patch("dictadesk.core.session.microphones", return_value=[]),
    patch(
        "dictadesk.desktop.commands.DesktopCommands.probe",
        return_value={s.capability: "" for s in registry()},
    ),
):
    later(start)
    app.run(["dictadesk-usage-test"])
assert not failures, failures
