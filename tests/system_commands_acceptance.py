# SPDX-License-Identifier: GPL-3.0-only
# ruff: noqa: E402
"""Private D-Bus/X11 system-command integrations; no host power/audio changes."""

import os

assert os.environ["HOME"].startswith("/tmp/dictadesk-test.")
os.environ["GSETTINGS_BACKEND"] = "memory"
from Xlib import X, Xatom, display
from dictadesk.ui.gtk import Gio, GLib
from dictadesk.desktop.commands import DesktopCommands
from dictadesk.desktop.destination import Target
from dictadesk.desktop.delivery import Destination
from dictadesk.desktop.window_commands import perform, screen_off

loop = GLib.MainLoop()
errors, invocations = [], []
connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
brightness = [50]
POWER = "org.cinnamon.SettingsDaemon.Power"
POWER_SCREEN = POWER + ".Screen"
MPRIS = "org.mpris.MediaPlayer2.fixture"
PLAYER = "org.mpris.MediaPlayer2.Player"
xml = f'''<node><interface name="{POWER_SCREEN}">
<method name="GetPercentage"><arg type="u" direction="out"/></method>
<method name="SetPercentage"><arg type="u" direction="in"/><arg type="u" direction="out"/></method>
</interface></node>'''


def power(conn, sender, path, iface, method, parameters, invocation):
    if method == "SetPercentage":
        brightness[0] = parameters.unpack()[0]
    invocations.append((iface, method))
    invocation.return_value(GLib.Variant("(u)", (brightness[0],)))


connection.register_object(
    "/org/cinnamon/SettingsDaemon/Power",
    Gio.DBusNodeInfo.new_for_xml(xml).interfaces[0],
    power,
    None,
    None,
)
owner1 = Gio.bus_own_name_on_connection(
    connection, POWER, Gio.BusNameOwnerFlags.NONE, None, None
)
media_xml = f'''<node><interface name="{PLAYER}">
<method name="Play"/><method name="Pause"/><method name="PlayPause"/><method name="Next"/><method name="Previous"/><method name="Stop"/>
<property name="PlaybackStatus" type="s" access="read"/>
<property name="CanControl" type="b" access="read"/>
<property name="CanPlay" type="b" access="read"/>
<property name="CanPause" type="b" access="read"/>
<property name="CanGoNext" type="b" access="read"/>
<property name="CanGoPrevious" type="b" access="read"/>
</interface></node>'''


def media(conn, sender, path, iface, method, parameters, invocation):
    invocations.append((iface, method))
    invocation.return_value(None)


connection.register_object(
    "/org/mpris/MediaPlayer2",
    Gio.DBusNodeInfo.new_for_xml(media_xml).interfaces[0],
    media,
    lambda conn, sender, path, iface, name: (
        GLib.Variant("s", "Playing") if name == "PlaybackStatus" else GLib.Variant("b", True)
    ),
    None,
)
owner2 = Gio.bus_own_name_on_connection(
    connection, MPRIS, Gio.BusNameOwnerFlags.NONE, None, None
)


def dispatch(callback):
    GLib.idle_add(lambda: (callback(), False)[1])


adapter = DesktopCommands(dispatch, connection=connection)


def checked(fn):
    try:
        fn()
    except Exception as exc:
        import traceback

        traceback.print_exc()
        errors.append(exc)
        loop.quit()
    return False


def begin():
    adapter.start()
    adapter.execute(
        "brightness",
        ("up", 20),
        lambda: True,
        lambda message: checked(lambda: changed(message)),
    )


def changed(message):
    assert brightness[0] == 70 and message == "", message
    adapter.execute(
        "media", ("Next",), lambda: True, lambda message: checked(lambda: media_done(message))
    )


def media_done(message):
    assert (PLAYER, "Next") in invocations and message == "", (invocations, message)
    settings, key = adapter.notification_settings()
    adapter.dnd((True,), lambda: True)
    assert not settings.get_boolean(key)
    adapter.dnd((False,), lambda: True)
    assert settings.get_boolean(key)
    windows()


def windows():
    x = display.Display()
    wm = display.Display()
    root = x.screen().root
    wm.screen().root.change_attributes(event_mask=X.SubstructureNotifyMask)
    wm.sync()
    atoms = (
        "_NET_ACTIVE_WINDOW",
        "_NET_CLOSE_WINDOW",
        "_NET_WM_STATE",
        "_NET_MOVERESIZE_WINDOW",
        "_NET_SHOWING_DESKTOP",
    )
    root.change_property(
        x.intern_atom("_NET_SUPPORTED"), Xatom.ATOM, 32, [x.intern_atom(name) for name in atoms]
    )
    original = root.create_window(0, 0, 500, 300, 0, x.screen().root_depth)
    other = root.create_window(600, 350, 400, 250, 0, x.screen().root_depth)
    for window in (original, other):
        window.change_property(x.intern_atom("_NET_WM_PID"), Xatom.CARDINAL, 32, [os.getpid()])
        window.change_property(x.intern_atom("_NET_WM_DESKTOP"), Xatom.CARDINAL, 32, [0])
        window.map()
    root.change_property(
        x.intern_atom("_NET_CLIENT_LIST_STACKING"), Xatom.WINDOW, 32, [original.id, other.id]
    )
    root.change_property(x.intern_atom("_NET_CURRENT_DESKTOP"), Xatom.CARDINAL, 32, [0])
    root.change_property(x.intern_atom("_NET_ACTIVE_WINDOW"), Xatom.WINDOW, 32, [other.id])
    x.set_input_focus(other, X.RevertToParent, X.CurrentTime)
    x.sync()
    target = Target(Destination(original.id, original.id, os.getpid()))
    for action in ("maximize", "minimize", "left", "right", "close"):
        assert "requested" in perform(action, target, lambda: True)
    assert "switch" in perform("switch", target, lambda: True).lower()
    x.sync()
    events = []
    while wm.pending_events():
        event = wm.next_event()
        if event.type == X.ClientMessage:
            events.append((x.get_atom_name(event.client_type), event.window.id))
    assert ("_NET_CLOSE_WINDOW", original.id) in events, events
    assert ("_NET_WM_STATE", original.id) in events
    assert ("WM_CHANGE_STATE", original.id) in events
    assert ("_NET_ACTIVE_WINDOW", other.id) in events
    assert not any(
        name in ("_NET_CLOSE_WINDOW", "_NET_WM_STATE", "WM_CHANGE_STATE") and ident == other.id
        for name, ident in events
    )
    original.destroy()
    x.sync()
    try:
        perform("close", target, lambda: True)
        raise AssertionError("Closed origin unexpectedly accepted")
    except ValueError:
        pass
    try:
        screen_off(lambda: True)
    except ValueError as exc:
        assert "unavailable" in str(exc) or "disabled" in str(exc)
    other.destroy()
    x.close()
    wm.close()
    adapter.close()
    print(
        "PASS private desktop commands: real D-Bus brightness/MPRIS, memory-only DND, original-window EWMH maximize/minimize/tile/close/switch, stale-window rejection and unavailable DPMS"
    )
    loop.quit()


GLib.timeout_add(300, lambda: checked(begin))
GLib.timeout_add_seconds(
    20,
    lambda: (errors.append(AssertionError("System commands timed out")), loop.quit(), False)[2],
)
try:
    loop.run()
finally:
    Gio.bus_unown_name(owner1)
    Gio.bus_unown_name(owner2)
assert not errors, errors
