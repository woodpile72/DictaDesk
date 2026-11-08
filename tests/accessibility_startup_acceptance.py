# SPDX-License-Identifier: GPL-3.0-only
# ruff: noqa: E402
"""A stale advertised AT-SPI socket must not crash the real GTK entrypoint."""

import os
import subprocess
import sys
import time

assert os.environ["HOME"].startswith("/tmp/dictadesk-test.")
from gi.repository import Gio, GLib
from Xlib import display, X

bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
xml = '<node><interface name="org.a11y.Bus"><method name="GetAddress"><arg type="s" direction="out"/></method></interface></node>'
requests = []


def address(conn, sender, path, interface, method, params, invocation):
    requests.append(method)
    invocation.return_value(
        GLib.Variant("(s)", ("unix:path=/tmp/dictadesk-nonexistent-a11y-socket",))
    )


bus.register_object(
    "/org/a11y/bus", Gio.DBusNodeInfo.new_for_xml(xml).interfaces[0], address, None, None
)
owner = Gio.bus_own_name_on_connection(
    bus, "org.a11y.Bus", Gio.BusNameOwnerFlags.NONE, None, None
)
loop = GLib.MainLoop()
process = None
errors = []
started = time.monotonic()
name = "io.github.woodpile72.DictaDesk"


def action(value):
    def replied(connection, result):
        try:
            connection.call_finish(result)
            GLib.timeout_add(100, finished)
        except Exception as exc:
            errors.append(exc)
            loop.quit()

    bus.call(
        name,
        "/io/github/woodpile72/DictaDesk",
        "org.gtk.Actions",
        "Activate",
        GLib.Variant("(sava{sv})", (value, [], {})),
        None,
        Gio.DBusCallFlags.NONE,
        3000,
        None,
        replied,
    )


def launch():
    global process
    process = subprocess.Popen(
        [sys.executable, "-m", "dictadesk", "--background"],
        env={**os.environ, "GTK_A11Y": "atspi", "PYTHONFAULTHANDLER": "1"},
    )
    GLib.timeout_add(100, check)
    return False


def check():
    try:
        assert time.monotonic() - started < 15, "Startup timed out"
        assert process.poll() is None, "GTK crashed on the stale accessibility socket"
        owned = bus.call_sync(
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            "NameHasOwner",
            GLib.Variant("(s)", (name,)),
            None,
            Gio.DBusCallFlags.NONE,
            1000,
            None,
        ).unpack()[0]
        if not owned:
            return True

        # Keep this process's main loop free to answer GetAddress while GTK and
        # its portal helpers start. A synchronous action call here deadlocks the
        # fake accessibility service that this test intentionally supplies.
        def ready(connection, result):
            try:
                connection.call_finish(result)
            except GLib.Error:
                GLib.timeout_add(100, check)
                return
            try:
                assert requests
                x = display.Display()
                mapped = [
                    w
                    for w in x.screen().root.query_tree().children
                    if w.get_wm_class()
                    and any("dictadesk" in part.lower() for part in w.get_wm_class())
                    and w.get_attributes().map_state == X.IsViewable
                ]
                assert not mapped, "Fallback opened a main window"
                x.close()
                action("quit")
            except Exception as exc:
                errors.append(exc)
                loop.quit()

        bus.call(
            name,
            "/StatusNotifierItem",
            "org.freedesktop.DBus.Properties",
            "Get",
            GLib.Variant("(ss)", ("org.kde.StatusNotifierItem", "ToolTip")),
            None,
            Gio.DBusCallFlags.NONE,
            500,
            None,
            ready,
        )
    except Exception as exc:
        errors.append(exc)
        loop.quit()
    return False


def finished():
    if process.poll() is None:
        return True
    if process.returncode != 0:
        errors.append(AssertionError(f"Unexpected exit status: {process.returncode}"))
    loop.quit()
    return False


GLib.timeout_add(200, launch)
try:
    loop.run()
finally:
    if process and process.poll() is None:
        process.terminate()
        process.wait(timeout=5)
    Gio.bus_unown_name(owner)
assert not errors, errors
print(
    "PASS real GTK entrypoint: stale accessibility socket, forced bridge enabled, safe tray-only fallback and clean shutdown"
)
