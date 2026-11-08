# SPDX-License-Identifier: GPL-3.0-only
"""Installed-package launch check; use a private HOME, bus, display and model cache."""

import os
from pathlib import Path
import subprocess
import sys
import time

assert os.environ["HOME"].startswith("/tmp/dictadesk-test.")
assert os.environ.get("DICTADESK_PACKAGE_TEST") == "1"
sys.path[:0] = ["/usr/lib/dictadesk", "/usr/lib/dictadesk/vendor"]
from dictadesk import APP_ID, __version__  # noqa: E402
from dictadesk.core.settings import SettingsStore  # noqa: E402
from dictadesk.core.models import model_directory  # noqa: E402
from gi.repository import Gio, GLib  # noqa: E402
from Xlib import display, X, XK  # noqa: E402
from Xlib.ext import xtest  # noqa: E402
import onnxruntime  # noqa: E402
import moonshine_voice  # noqa: E402, F401

assert __version__ == os.environ.get("DICTADESK_EXPECT_VERSION", "1.2.0")
assert onnxruntime.get_available_providers()
defaults = SettingsStore().read()
assert defaults.local_model == "parakeet"
assert not defaults.hands_free and defaults.wake_phrase == "Clanker"
assert defaults.command_sound
assert Path("/usr/lib/dictadesk/dictadesk/resources/dictadesk-panel-wake-color.svg").is_file()
assert Path(
    "/usr/lib/dictadesk/dictadesk/resources/dictadesk-panel-wake-symbolic.svg"
).is_file()
assert str(model_directory(create=True)).startswith("/var/cache/dictadesk/")
bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
app_path = "/" + APP_ID.replace(".", "/")


def owns():
    return bus.call_sync(
        "org.freedesktop.DBus",
        "/org/freedesktop/DBus",
        "org.freedesktop.DBus",
        "NameHasOwner",
        GLib.Variant("(s)", (APP_ID,)),
        None,
        Gio.DBusCallFlags.NONE,
        3000,
        None,
    ).unpack()[0]


def action(name):
    bus.call_sync(
        APP_ID,
        app_path,
        "org.gtk.Actions",
        "Activate",
        GLib.Variant("(sava{sv})", (name, [], {})),
        None,
        Gio.DBusCallFlags.NONE,
        3000,
        None,
    )


assert not owns()
process = subprocess.Popen(["/usr/bin/dictadesk", "--background"])
try:
    deadline = time.monotonic() + 15
    while not owns() and time.monotonic() < deadline:
        assert process.poll() is None
        time.sleep(0.1)
    assert owns()
    connection = display.Display()
    assert not any(
        w.get_wm_class()
        and any("dictadesk" in c.lower() for c in w.get_wm_class())
        and w.get_attributes().map_state == X.IsViewable
        for w in connection.screen().root.query_tree().children
    ), "Startup displayed a window"
    action("preferences")
    time.sleep(0.5)
    mapped = [
        w
        for w in connection.screen().root.query_tree().children
        if w.get_wm_class()
        and any("dictadesk" in c.lower() for c in w.get_wm_class())
        and w.get_attributes().map_state == X.IsViewable
    ]
    assert len(mapped) == 1 and mapped[0].get_wm_name() == "Preferences", (
        "Tray Preferences unexpectedly opened the main window"
    )
    connection.set_input_focus(mapped[0], X.RevertToParent, X.CurrentTime)
    escape = connection.keysym_to_keycode(XK.string_to_keysym("Escape"))
    xtest.fake_input(connection, X.KeyPress, escape)
    xtest.fake_input(connection, X.KeyRelease, escape)
    connection.sync()
    time.sleep(0.3)
    # A fresh profile first dismisses the explicit model-download prompt.
    # A second Escape then closes Preferences itself.
    remaining = [
        w
        for w in connection.screen().root.query_tree().children
        if w.get_wm_name() == "Preferences" and w.get_attributes().map_state == X.IsViewable
    ]
    if remaining:
        xtest.fake_input(connection, X.KeyPress, escape)
        xtest.fake_input(connection, X.KeyRelease, escape)
        connection.sync()
        time.sleep(0.3)
    assert not any(
        w.get_wm_class()
        and any("dictadesk" in c.lower() for c in w.get_wm_class())
        and w.get_attributes().map_state == X.IsViewable
        for w in connection.screen().root.query_tree().children
    ), "Closing tray Preferences revealed the main window"
    action("show")
    time.sleep(0.5)
    visible = [
        w
        for w in connection.screen().root.query_tree().children
        if w.get_wm_class()
        and any("dictadesk" in c.lower() for c in w.get_wm_class())
        and w.get_attributes().map_state == X.IsViewable
    ]
    assert len(visible) == 1 and visible[0].get_wm_name() == "Preferences"
    action("about")
    time.sleep(0.3)
    action("preferences")
    time.sleep(0.3)
    preferences = [
        w
        for w in connection.screen().root.query_tree().children
        if w.get_wm_name() == "Preferences" and w.get_attributes().map_state == X.IsViewable
    ]
    assert len(preferences) == 1, "Preferences did not open its own window"
    assert preferences[0].get_geometry().height >= 800
    action("usage")
    time.sleep(0.5)
    assert any(
        w.get_wm_name() == "DictaDesk Help" and w.get_attributes().map_state == X.IsViewable
        for w in connection.screen().root.query_tree().children
    )
    tooltip = bus.call_sync(
        APP_ID,
        "/StatusNotifierItem",
        "org.freedesktop.DBus.Properties",
        "Get",
        GLib.Variant("(ss)", ("org.kde.StatusNotifierItem", "ToolTip")),
        None,
        Gio.DBusCallFlags.NONE,
        3000,
        None,
    ).unpack()[0]
    assert tooltip[2] == "DictaDesk"
    connection.close()
    action("quit")
    assert process.wait(timeout=10) == 0
    assert not owns()
    assert not list(model_directory().rglob("*.onnx"))
    print(
        "PASS installed 1.2.0: runtime imports, tray-only startup, About, Preferences, reference window, tray and clean shutdown"
    )
finally:
    if process.poll() is None:
        process.terminate()
        process.wait(timeout=10)
