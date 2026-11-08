# SPDX-License-Identifier: GPL-3.0-only
"""EWMH requests addressed to the window remembered at wake activation."""

import time
from contextlib import closing

from Xlib import X, Xutil, display, protocol
from Xlib.ext import dpms  # noqa: F401

from .delivery import destination


def prop(connection, window, name):
    value = window.get_full_property(connection.intern_atom(name), X.AnyPropertyType)
    return list(value.value) if value is not None else []


def valid_window(connection, window):
    if not window or not window.pid:
        return False
    try:
        client = connection.create_resource_object("window", window.client)
        client.get_attributes()
        return prop(connection, client, "_NET_WM_PID") == [window.pid]
    except Exception:
        return False


def request(connection, ident, name, data):
    supported = prop(connection, connection.screen().root, "_NET_SUPPORTED")
    atom = connection.intern_atom(name)
    if name != "WM_CHANGE_STATE" and atom not in supported:
        raise ValueError("The window manager does not support this operation.")
    event = protocol.event.ClientMessage(
        window=ident,
        client_type=atom,
        data=(32, [(value & 0xFFFFFFFF) for value in (data + [0] * 5)[:5]]),
    )
    connection.screen().root.send_event(
        event, event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask
    )
    connection.sync()


def activate(connection, window, valid):
    if not valid_window(connection, window):
        raise ValueError("The original window is no longer available.")
    request(connection, window.client, "_NET_ACTIVE_WINDOW", [2, X.CurrentTime, 0])
    deadline = time.monotonic() + 1.5
    while valid() and time.monotonic() < deadline:
        current = destination(connection)
        if current and current.client == window.client and current.pid == window.pid:
            return
        time.sleep(0.025)
    raise ValueError("The original window could not be activated safely.")


def focused_operation(target, valid, operation):
    with closing(display.Display()) as connection:
        previous = destination(connection)
        activate(connection, target.window if target else None, valid)
        try:
            if not valid():
                return "Cancelled."
            return operation(connection)
        finally:
            try:
                current = destination(connection)
                if (
                    valid()
                    and previous
                    and current
                    and target
                    and current.client == target.window.client
                    and previous.client != current.client
                    and valid_window(connection, previous)
                ):
                    request(
                        connection, previous.client, "_NET_ACTIVE_WINDOW", [2, X.CurrentTime, 0]
                    )
            except Exception:
                pass  # Returning focus cannot invalidate a completed capture.


def perform(action, target, valid):
    with closing(display.Display()) as connection:
        root = connection.screen().root
        if not valid():
            return "Cancelled."
        if not prop(connection, root, "_NET_SUPPORTED"):
            raise ValueError("No compatible EWMH window manager is available.")
        if action == "desktop":
            request(connection, root.id, "_NET_SHOWING_DESKTOP", [1])
            return "Show desktop requested."
        window = target.window if target else None
        if not valid_window(connection, window):
            raise ValueError("The original window is no longer available.")
        ident = window.client
        if action == "close":
            request(connection, ident, "_NET_CLOSE_WINDOW", [X.CurrentTime, 2])
        elif action == "maximize":
            request(
                connection,
                ident,
                "_NET_WM_STATE",
                [
                    1,
                    connection.intern_atom("_NET_WM_STATE_MAXIMIZED_VERT"),
                    connection.intern_atom("_NET_WM_STATE_MAXIMIZED_HORZ"),
                    2,
                ],
            )
        elif action == "minimize":
            request(connection, ident, "WM_CHANGE_STATE", [Xutil.IconicState])
        elif action == "switch":
            clients = prop(connection, root, "_NET_CLIENT_LIST_STACKING")
            desktop = prop(connection, root, "_NET_CURRENT_DESKTOP")
            for candidate in reversed(clients):
                if candidate == ident:
                    continue
                other = connection.create_resource_object("window", candidate)
                if desktop and prop(connection, other, "_NET_WM_DESKTOP") not in (
                    desktop,
                    [0xFFFFFFFF],
                ):
                    continue
                states = prop(connection, other, "_NET_WM_STATE")
                if connection.intern_atom("_NET_WM_STATE_SKIP_TASKBAR") in states:
                    continue
                request(connection, candidate, "_NET_ACTIVE_WINDOW", [2, X.CurrentTime, 0])
                return "Window switch requested."
            raise ValueError("No other application window is available.")
        elif action in ("left", "right"):
            client = connection.create_resource_object("window", ident)
            geometry = client.get_geometry()
            position = root.translate_coords(client, 0, 0)
            x, y, width, height = (
                0,
                0,
                connection.screen().width_in_pixels,
                connection.screen().height_in_pixels,
            )
            try:
                monitors = root.xrandr_get_monitors(True).monitors
                cx, cy = position.x + geometry.width / 2, position.y + geometry.height / 2
                monitor = next(
                    m
                    for m in monitors
                    if m.x <= cx < m.x + m.width_in_pixels
                    and m.y <= cy < m.y + m.height_in_pixels
                )
                x, y, width, height = (
                    monitor.x,
                    monitor.y,
                    monitor.width_in_pixels,
                    monitor.height_in_pixels,
                )
            except Exception:
                pass
            workarea = prop(connection, root, "_NET_WORKAREA")
            desktop = (prop(connection, root, "_NET_CURRENT_DESKTOP") or [0])[0]
            if len(workarea) >= (desktop + 1) * 4:
                wx, wy, ww, wh = workarea[desktop * 4 : desktop * 4 + 4]
                right, bottom = min(x + width, wx + ww), min(y + height, wy + wh)
                x, y = max(x, wx), max(y, wy)
                width, height = right - x, bottom - y
            extents = prop(connection, client, "_NET_FRAME_EXTENTS") or [0, 0, 0, 0]
            request(
                connection,
                ident,
                "_NET_WM_STATE",
                [
                    0,
                    connection.intern_atom("_NET_WM_STATE_MAXIMIZED_VERT"),
                    connection.intern_atom("_NET_WM_STATE_MAXIMIZED_HORZ"),
                    2,
                ],
            )
            half = width // 2
            request(
                connection,
                ident,
                "_NET_MOVERESIZE_WINDOW",
                [
                    1 | (15 << 8) | (2 << 12),
                    x + (half if action == "right" else 0),
                    y,
                    max(1, half - extents[0] - extents[1]),
                    max(1, height - extents[2] - extents[3]),
                ],
            )
        else:
            raise ValueError("Unsupported window operation.")
        return "Window operation requested."


def screen_off(valid):
    with closing(display.Display()) as connection:
        if not connection.has_extension("DPMS") or not connection.dpms_capable().capable:
            raise ValueError("Display power control is unavailable on this X11 server.")
        if not connection.dpms_info().state:
            raise ValueError("DPMS is disabled in the desktop settings.")
        if valid():
            connection.dpms_force_level(dpms.DPMSModeOff)
            connection.sync()
        return "Display power-off requested."
