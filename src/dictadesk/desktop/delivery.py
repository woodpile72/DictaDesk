# SPDX-License-Identifier: GPL-3.0-only
"""Conservative X11 paste destination checks. Never activate a target window."""

from dataclasses import dataclass

from Xlib import XK, X
from Xlib.ext import xtest


@dataclass(frozen=True)
class Destination:
    client: int
    focus: int
    pid: int


def destination(connection):
    root = connection.screen().root
    active = root.get_full_property(
        connection.intern_atom("_NET_ACTIVE_WINDOW"), X.AnyPropertyType
    )
    focus = connection.get_input_focus().focus
    if not hasattr(focus, "id") or focus.id == root.id:
        return None
    ident = int(active.value[0]) if active is not None and len(active.value) else focus.id
    window = connection.create_resource_object("window", ident)
    prop = window.get_full_property(connection.intern_atom("_NET_WM_PID"), X.AnyPropertyType)
    return Destination(ident, focus.id, int(prop.value[0]) if prop is not None else 0)


def paste(connection, expected):
    if expected is None or destination(connection) != expected:
        return False
    control = connection.keysym_to_keycode(XK.string_to_keysym("Control_L"))
    key = connection.keysym_to_keycode(XK.string_to_keysym("v"))
    # Do not clear modifiers: a held physical key must postpone delivery.
    xtest.fake_input(connection, X.KeyPress, control)
    xtest.fake_input(connection, X.KeyPress, key)
    xtest.fake_input(connection, X.KeyRelease, key)
    xtest.fake_input(connection, X.KeyRelease, control)
    connection.sync()
    return True
