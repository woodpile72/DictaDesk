# SPDX-License-Identifier: GPL-3.0-only
"""Private D-Bus notification server for isolated desktop acceptance tests."""

from dictadesk.ui.gtk import Gio, GLib

XML = """<node><interface name="org.freedesktop.Notifications">
<method name="Notify"><arg type="s" direction="in"/><arg type="u" direction="in"/>
<arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="s" direction="in"/>
<arg type="as" direction="in"/><arg type="a{sv}" direction="in"/><arg type="i" direction="in"/>
<arg type="u" direction="out"/></method>
<method name="GetCapabilities"><arg type="as" direction="out"/></method>
<method name="GetServerInformation"><arg type="s" direction="out"/><arg type="s" direction="out"/>
<arg type="s" direction="out"/><arg type="s" direction="out"/></method>
<method name="CloseNotification"><arg type="u" direction="in"/></method>
<signal name="ActionInvoked"><arg type="u"/><arg type="s"/></signal>
</interface></node>"""


class NotificationHost:
    def __init__(self):
        self.messages = []
        self.ids = []
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.bus.call_sync(
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            "RequestName",
            GLib.Variant("(su)", ("org.freedesktop.Notifications", 0)),
            None,
            Gio.DBusCallFlags.NONE,
            3000,
            None,
        )
        self.bus.register_object(
            "/org/freedesktop/Notifications",
            Gio.DBusNodeInfo.new_for_xml(XML).interfaces[0],
            self.call,
            None,
            None,
        )

    def call(self, connection, sender, path, iface, method, parameters, invocation):
        if method == "Notify":
            values = parameters.unpack()
            self.messages.append(values)
            ident = values[1] or (42 + len(set(self.ids)))
            self.ids.append(ident)
            invocation.return_value(GLib.Variant("(u)", (ident,)))
        elif method == "GetCapabilities":
            invocation.return_value(GLib.Variant("(as)", (["body", "actions"],)))
        elif method == "GetServerInformation":
            invocation.return_value(GLib.Variant("(ssss)", ("Fixture", "DictaDesk", "1", "1.2")))
        else:
            invocation.return_value(GLib.Variant("()", ()))

    def activate_last(self):
        self.bus.emit_signal(
            None,
            "/org/freedesktop/Notifications",
            "org.freedesktop.Notifications",
            "ActionInvoked",
            GLib.Variant("(us)", (self.ids[-1], "default")),
        )
