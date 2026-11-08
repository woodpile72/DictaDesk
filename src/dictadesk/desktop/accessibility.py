# SPDX-License-Identifier: GPL-3.0-only
"""Verify the user accessibility bus before GTK can initialize its bridge."""

import os
import threading

from gi.repository import Gio, GLib


UNAVAILABLE = (
    "The desktop accessibility bus is unavailable. Automatic field insertion is disabled; "
    "dictation remains available through clipboard recovery. Restart the desktop "
    "accessibility service or sign out and back in to restore field tracking."
)


def prepare_accessibility():
    """Prefer the session service over a potentially stale X11 AT_SPI_BUS property.

    GTK's bridge can abort the process when an advertised socket has disappeared.
    A bounded connection check lets the tray and recovery continue instead.
    """
    cancel = Gio.Cancellable()
    deadline = threading.Timer(1.5, cancel.cancel)
    deadline.daemon = True
    deadline.start()
    try:
        session = Gio.bus_get_sync(Gio.BusType.SESSION, cancel)
        address = session.call_sync(
            "org.a11y.Bus",
            "/org/a11y/bus",
            "org.a11y.Bus",
            "GetAddress",
            None,
            GLib.VariantType.new("(s)"),
            Gio.DBusCallFlags.NONE,
            1000,
            cancel,
        ).unpack()[0]
        connection = Gio.DBusConnection.new_for_address_sync(
            address,
            Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
            | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
            None,
            cancel,
        )
        connection.close_sync(None)
        os.environ["AT_SPI_BUS_ADDRESS"] = address
        return ""
    except GLib.Error:
        os.environ["GTK_A11Y"] = "none"
        return UNAVAILABLE
    finally:
        deadline.cancel()
