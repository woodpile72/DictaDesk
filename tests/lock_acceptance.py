# SPDX-License-Identifier: GPL-3.0-only
# ruff: noqa: E402
"""Fake lock service on a private bus: never lock the user's desktop or record."""

import os
from pathlib import Path
import threading
from types import SimpleNamespace
from unittest.mock import patch

assert os.environ["HOME"].startswith("/tmp/dictadesk-test.")
from dictadesk.ui.application import Application
from dictadesk.ui.gtk import Adw, Gtk, Gio, GLib
from dictadesk.desktop.lock import ScreenLock, SERVICES

Adw.init()
Gtk.Settings.get_default().set_property("gtk-enable-animations", False)
app = Application(isolated=True)
errors = []
name, path = SERVICES[0]
bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
xml = f'<node><interface name="{name}"><method name="GetActive"><arg type="b" direction="out"/></method><signal name="ActiveChanged"><arg type="b"/></signal></interface></node>'
active = False
service = bus.register_object(
    path,
    Gio.DBusNodeInfo.new_for_xml(xml).interfaces[0],
    lambda c, s, p, i, m, v, call: call.return_value(GLib.Variant("(b)", (active,))),
    None,
    None,
)
owner = Gio.bus_own_name_on_connection(bus, name, Gio.BusNameOwnerFlags.NONE, None, None)
root = Path(os.environ["HOME"])


def later(fn):
    def run():
        try:
            fn()
        except Exception as exc:
            import traceback

            traceback.print_exc()
            errors.append(exc)
            app.session.phase = "idle"
            app.session.capture = None
            app.request_quit()
        return False

    GLib.timeout_add(400, run)


def start():
    app.session.microphone_problem = ""
    app.session.recognizer.loaded = True
    app.session.preferences.hands_free = True
    app.screen_lock = ScreenLock(app.screen_lock_changed, connection=bus, services=SERVICES[:1])
    later(unlocked)


def unlocked():
    assert not app.session.desktop_blocked and app.session.can_record
    app.handsfree.stop.clear()
    entries = []
    for n in (1, 2):
        entry = root / f"lock{n}.desktop"
        entry.write_text(
            f"[Desktop Entry]\nType=Application\nName=Fixture {n}\nExec=/usr/bin/touch {root}/launched{n}\n"
        )
        entries.append(Gio.DesktopAppInfo.new_from_filename(str(entry)))
    with patch("dictadesk.ui.application.Gio.AppInfo.get_all", return_value=entries):
        app.launch_command(("fixture",), app.handsfree.generation, heard="Open fixture")
    assert app.command_chooser
    app.old_choice = app.handsfree.choice
    app.old_generation = app.handsfree.generation
    app.pending_start = ("record", None)
    app.session.phase = "listening"
    app.session.capture = SimpleNamespace(cancel=threading.Event(), stop=threading.Event())
    signal(True)
    later(locked)


def signal(value):
    global active
    active = value
    bus.emit_signal(None, path, name, "ActiveChanged", GLib.Variant("(b)", (value,)))


def locked():
    c = app.session
    assert c.desktop_locked and c.desktop_blocked and not c.can_record
    assert c.cancelled.is_set() and c.capture.cancel.is_set()
    assert app.pending_start is None and app.command_chooser is None
    assert app.old_choice[2].is_set()
    app.old_choice[1](1, "one")
    with patch("dictadesk.ui.application.Gio.AppInfo.get_all") as apps:
        app.launch_command(("fixture",), app.old_generation, heard="Open fixture")
        apps.assert_not_called()
    with (
        patch.object(c, "record_continuous") as record,
    ):
        app.key_pressed("record")
        app.start_from_shortcut("record")
        app.start_from_tray("record")
        record.assert_not_called()
    c.phase = "idle"
    c.capture = None
    signal(False)
    later(unlocked_again)


def unlocked_again():
    c = app.session
    assert not c.desktop_locked and not c.desktop_blocked and c.can_record
    app.handsfree.stop.clear()
    app.old_choice[1](1, "one")
    assert not (root / "launched1").exists() and not (root / "launched2").exists()
    assert app.pending_start is None
    print(
        "PASS private D-Bus lock/unlock: capture cancellation, pending-start reset, chooser dismissal, stale-result rejection and recording guards"
    )
    app.request_quit()


with (
    patch("dictadesk.core.session.Session.scan_devices"),
    patch("dictadesk.core.session.Session.load_available_model"),
    patch("dictadesk.core.handsfree.HandsFree.tick"),
):
    later(start)
    app.run(["dictadesk-lock-test"])
Gio.bus_unown_name(owner)
bus.unregister_object(service)
assert not errors, errors
