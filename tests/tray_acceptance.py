# SPDX-License-Identifier: GPL-3.0-only
# ruff: noqa: E402
"""StatusNotifier and DBusMenu exercised against a private host, not the real panel."""

import os
from unittest.mock import patch

assert os.environ["HOME"].startswith("/tmp/dictadesk-test.")
from dictadesk.ui.application import Application
from dictadesk.ui.gtk import Gio, GLib
from dictadesk.desktop.tray import Tray

XML = """<node><interface name="org.kde.StatusNotifierWatcher">
<method name="RegisterStatusNotifierItem"><arg type="s" direction="in"/></method>
<property name="IsStatusNotifierHostRegistered" type="b" access="read"/>
<signal name="StatusNotifierHostRegistered"/><signal name="StatusNotifierHostUnregistered"/>
</interface></node>"""
app = Application(isolated=True)
errors, registrations = [], []
connection = None
owner = None
host = True


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

    GLib.timeout_add(400, run)


def begin():
    global connection, owner
    connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)

    def register(conn, sender, path, iface, method, args, invocation):
        registrations.append(args.unpack()[0])
        invocation.return_value(None)

    connection.register_object(
        "/StatusNotifierWatcher",
        Gio.DBusNodeInfo.new_for_xml(XML).interfaces[0],
        register,
        lambda *args: GLib.Variant("b", host),
        None,
    )
    owner = Gio.bus_own_name_on_connection(
        connection, "org.kde.StatusNotifierWatcher", Gio.BusNameOwnerFlags.NONE, None, None
    )
    app.tray = Tray(app, app.resources)
    later(registered)


def registered():
    assert registrations and app.tray.ready
    app.session.microphone_problem = ""
    app.session.recognizer.loaded = False
    with patch("dictadesk.core.session.downloaded", return_value=True):
        app.refresh()
        assert app.tray.title == "Standby" and app.tray.state == "idle"
        tooltip = app.tray.item_property(None, None, None, None, "ToolTip").unpack()
        assert tooltip[3] == "Standby", tooltip
    app.session.phase = "loading"
    app.refresh()
    assert app.tray.title == "Loading" and app.tray.state == "loading"
    assert app.tray.icon() == "dictadesk-panel-loading-symbolic"
    assert app.tray.pixmap()
    app.session.phase = "transcribing"
    app.refresh()
    assert app.tray.state == "processing"
    app.session.phase = "idle"

    def got(conn, result):
        try:
            revision, layout = conn.call_finish(result).unpack()
            assert layout[0] == 0 and len(layout[2]) == 5, layout
            labels = [node[1]["label"] for node in layout[2]]
            assert "Show DictaDesk" in labels and "Quit" in labels
            later(lose_host)
        except Exception as exc:
            errors.append(exc)
            app.request_quit()

    connection.call(
        connection.get_unique_name(),
        "/Menu",
        "com.canonical.dbusmenu",
        "GetLayout",
        GLib.Variant("(iias)", (0, -1, [])),
        None,
        Gio.DBusCallFlags.NONE,
        2000,
        None,
        got,
    )


def lose_host():
    global host
    app.window.set_visible(False)
    assert not app.window.get_visible()
    host = False
    connection.emit_signal(
        None,
        "/StatusNotifierWatcher",
        "org.kde.StatusNotifierWatcher",
        "StatusNotifierHostUnregistered",
        None,
    )
    later(restored)


def restored():
    assert not app.tray.ready
    assert app.window.get_visible(), (
        "Hidden window was not recovered after the panel disappeared"
    )
    Gio.bus_unown_name(owner)
    later(done)


def done():
    assert not app.tray.ready
    print("PASS tray registration, menu layout, host-loss recovery and watcher loss")
    app.request_quit()


with (
    patch("dictadesk.core.session.check"),
    patch("dictadesk.core.session.microphones", return_value=[]),
):
    later(begin)
    app.run(["dictadesk-tray-test"])
assert not errors, errors
