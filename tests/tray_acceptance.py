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
app.model_prompts_shown.add(("parakeet", "int8"))
errors, registrations = [], []
connection = None
owner = None
host = True
maps = []


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
    assert not app.get_windows()
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
    app.refresh()

    def got(conn, result):
        try:
            revision, layout = conn.call_finish(result).unpack()
            assert layout[0] == 0 and len(layout[2]) == 8, layout
            labels = [node[1]["label"] for node in layout[2]]
            assert "Last Transcript…" not in labels and "Quit" in labels
            assert "Copy Uninserted Text" not in labels
            later(open_preferences)
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


def open_preferences():
    connection.call(
        connection.get_unique_name(),
        "/StatusNotifierItem",
        "org.kde.StatusNotifierItem",
        "ContextMenu",
        GLib.Variant("(ii)", (0, 0)),
        None,
        Gio.DBusCallFlags.NONE,
        2000,
        None,
        None,
    )
    connection.call(
        connection.get_unique_name(),
        "/Menu",
        "com.canonical.dbusmenu",
        "Event",
        GLib.Variant("(isvu)", (4, "clicked", GLib.Variant("s", ""), 0)),
        None,
        Gio.DBusCallFlags.NONE,
        2000,
        None,
        None,
    )
    later(preferences_open)


def preferences_open():
    p = app.preferences_dialog
    assert p and p.dialog.get_mapped()
    assert p.dialog.get_transient_for() is None
    assert not hasattr(app, "window") and not maps
    monitor = p.dialog.get_display().get_monitor_at_surface(p.dialog.get_surface())
    assert p.dialog.get_height() <= monitor.get_geometry().height - 96
    app.tray.event(4, "clicked")
    assert app.preferences_dialog is p
    p.dialog.close()
    later(preferences_closed)


def preferences_closed():
    assert app.preferences_dialog is None
    assert not hasattr(app, "window") and not maps
    app.tray.event(4, "clicked")
    later(save_hidden_preferences)


def save_hidden_preferences():
    p = app.preferences_dialog
    assert p.dialog.get_mapped() and not hasattr(app, "window")
    with patch.object(
        app, "save_preferences", side_effect=lambda prefs, key, close: close()
    ) as saved:
        p.save_clicked()
        saved.assert_called_once()
    assert app.preferences_dialog is None
    assert not hasattr(app, "window")
    later(lose_host)


def lose_host():
    global host
    assert not app.get_windows()
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
    assert not app.get_windows(), "Tray loss must not create a main window"
    Gio.bus_unown_name(owner)
    later(done)


def done():
    assert not app.tray.ready
    print(
        "PASS tray registration, Preferences-only activation/close/save, host-loss tray-only behavior and watcher loss"
    )
    app.request_quit()


with (
    patch("dictadesk.core.session.check"),
    patch("dictadesk.ui.preferences.read_key", return_value=""),
    patch(
        "dictadesk.ui.preferences.compute_capabilities",
        return_value={"cuda": False, "reason": "CPU test"},
    ),
    patch("dictadesk.core.session.microphones", return_value=[]),
):
    later(begin)
    app.run(["dictadesk-tray-test", "--background"])
assert not errors, errors
