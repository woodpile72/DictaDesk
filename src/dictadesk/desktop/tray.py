# SPDX-License-Identifier: GPL-3.0-only
"""StatusNotifierItem and DBusMenu, implemented directly with Gio."""

from pathlib import Path

from ..ui.gtk import Gio, GLib

ITEM = """<node><interface name="org.kde.StatusNotifierItem">
<method name="Activate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
<method name="SecondaryActivate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
<method name="ContextMenu"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
<method name="Scroll"><arg type="i" direction="in"/><arg type="s" direction="in"/></method>
<property name="Category" type="s" access="read"/><property name="Id" type="s" access="read"/>
<property name="Title" type="s" access="read"/><property name="Status" type="s" access="read"/>
<property name="IconName" type="s" access="read"/><property name="IconThemePath" type="s" access="read"/>
<property name="IconPixmap" type="a(iiay)" access="read"/><property name="ItemIsMenu" type="b" access="read"/>
<property name="Menu" type="o" access="read"/><property name="WindowId" type="i" access="read"/>
<property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
<signal name="NewIcon"/><signal name="NewToolTip"/><signal name="NewStatus"><arg type="s"/></signal>
</interface></node>"""
MENU = """<node><interface name="com.canonical.dbusmenu">
<method name="GetLayout"><arg type="i" direction="in"/><arg type="i" direction="in"/><arg type="as" direction="in"/><arg type="u" direction="out"/><arg type="(ia{sv}av)" direction="out"/></method>
<method name="GetGroupProperties"><arg type="ai" direction="in"/><arg type="as" direction="in"/><arg type="a(ia{sv})" direction="out"/></method>
<method name="GetProperty"><arg type="i" direction="in"/><arg type="s" direction="in"/><arg type="v" direction="out"/></method>
<method name="Event"><arg type="i" direction="in"/><arg type="s" direction="in"/><arg type="v" direction="in"/><arg type="u" direction="in"/></method>
<method name="EventGroup"><arg type="a(isvu)" direction="in"/><arg type="ai" direction="out"/></method>
<method name="AboutToShow"><arg type="i" direction="in"/><arg type="b" direction="out"/></method>
<method name="AboutToShowGroup"><arg type="ai" direction="in"/><arg type="ai" direction="out"/><arg type="ai" direction="out"/></method>
<property name="Version" type="u" access="read"/><property name="TextDirection" type="s" access="read"/>
<property name="Status" type="s" access="read"/><property name="IconThemePath" type="as" access="read"/>
<signal name="LayoutUpdated"><arg type="u"/><arg type="i"/></signal>
</interface></node>"""


class Tray:
    def __init__(self, application, resource_dir):
        self.app = application
        self.resources = Path(resource_dir)
        self.ready = False
        self.owner = None
        self.revision = 0
        self.state = "unavailable"
        self.title = "Setup needed"
        self.symbolic = True
        self.pixmaps = {}
        self.connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.items = [
            self.connection.register_object(
                "/StatusNotifierItem",
                Gio.DBusNodeInfo.new_for_xml(ITEM).interfaces[0],
                self.item_call,
                self.item_property,
                None,
            ),
            self.connection.register_object(
                "/Menu",
                Gio.DBusNodeInfo.new_for_xml(MENU).interfaces[0],
                self.menu_call,
                self.menu_property,
                None,
            ),
        ]
        self.watch = Gio.bus_watch_name(
            Gio.BusType.SESSION,
            "org.kde.StatusNotifierWatcher",
            Gio.BusNameWatcherFlags.NONE,
            self.appeared,
            self.vanished,
        )
        self.subscription = self.connection.signal_subscribe(
            None,
            "org.freedesktop.DBus.Properties",
            "PropertiesChanged",
            "/StatusNotifierWatcher",
            None,
            Gio.DBusSignalFlags.NONE,
            self.host_changed,
        )
        self.host_signal = self.connection.signal_subscribe(
            None,
            "org.kde.StatusNotifierWatcher",
            None,
            "/StatusNotifierWatcher",
            None,
            Gio.DBusSignalFlags.NONE,
            self.host_changed,
        )

    def appeared(self, connection, name, owner):
        self.owner = owner
        self.check_host()

    def check_host(self):
        owner = self.owner
        if not owner:
            return

        def checked(connection, result):
            if owner != self.owner:
                return
            try:
                available = connection.call_finish(result).unpack()[0]
            except GLib.Error:
                available = False
            if not available:
                self.set_ready(False)
                return

            def registered(conn, res):
                if owner != self.owner:
                    return
                try:
                    conn.call_finish(res)
                    self.set_ready(True)
                except GLib.Error:
                    self.set_ready(False)

            connection.call(
                owner,
                "/StatusNotifierWatcher",
                "org.kde.StatusNotifierWatcher",
                "RegisterStatusNotifierItem",
                GLib.Variant("(s)", (connection.get_unique_name(),)),
                None,
                Gio.DBusCallFlags.NONE,
                2000,
                None,
                registered,
            )

        self.connection.call(
            owner,
            "/StatusNotifierWatcher",
            "org.freedesktop.DBus.Properties",
            "Get",
            GLib.Variant(
                "(ss)", ("org.kde.StatusNotifierWatcher", "IsStatusNotifierHostRegistered")
            ),
            GLib.VariantType.new("(v)"),
            Gio.DBusCallFlags.NONE,
            2000,
            None,
            checked,
        )

    def host_changed(self, connection, sender, path, interface, signal, params):
        if sender == self.owner:
            self.check_host()

    def vanished(self, *_):
        self.owner = None
        self.set_ready(False)

    def set_ready(self, ready):
        self.ready = ready

    def icon(self):
        if self.state == "wake":
            # Keep the blue badge colored: symbolic recoloring only supports
            # the host's success/warning/error palette.
            return "dictadesk-panel-wake-color"
        suffix = "-symbolic" if self.symbolic else "-color"
        state = "recording" if self.state == "listening" else self.state
        return "dictadesk-panel-" + state + suffix

    def pixmap(self):
        name = self.icon()
        if name not in self.pixmaps:
            import gi

            gi.require_version("GdkPixbuf", "2.0")
            from gi.repository import GdkPixbuf

            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                str(self.resources / (name + ".svg")), 32, 32, True
            )
            data = pixbuf.get_pixels()
            channels, stride = pixbuf.get_n_channels(), pixbuf.get_rowstride()
            argb = bytearray()
            for y in range(pixbuf.get_height()):
                for x in range(pixbuf.get_width()):
                    i = y * stride + x * channels
                    r, g, b = data[i : i + 3]
                    a = data[i + 3] if channels == 4 else 255
                    argb.extend((a, r, g, b))
            self.pixmaps[name] = [(pixbuf.get_width(), pixbuf.get_height(), bytes(argb))]
        return self.pixmaps[name]

    def item_property(self, connection, sender, path, interface, prop):
        values = {
            "Category": ("s", "ApplicationStatus"),
            "Id": ("s", "DictaDesk"),
            "Title": ("s", "DictaDesk"),
            "Status": ("s", "Active"),
            "IconName": ("s", self.icon()),
            "IconThemePath": ("s", str(self.resources)),
            "IconPixmap": ("a(iiay)", self.pixmap()),
            "ItemIsMenu": ("b", False),
            "Menu": ("o", "/Menu"),
            "WindowId": ("i", 0),
            "ToolTip": ("(sa(iiay)ss)", (self.icon(), [], "DictaDesk", self.title)),
        }
        return GLib.Variant(*values[prop])

    def item_call(self, connection, sender, path, interface, method, args, invocation):
        if method in ("Activate", "SecondaryActivate"):
            self.app.show_preferences()
        invocation.return_value(None)

    def rows(self):
        c = self.app.session
        return {
            1: ("Last Transcript…", "transcript", bool(c.result.text or c.pending_text)),
            2: (
                "Stop Recording" if c.phase == "listening" else "Start Recording",
                "record",
                c.can_record or c.phase == "listening",
            ),
            3: ("Cancel", "cancel", c.busy),
            4: ("Preferences", "preferences", not c.busy),
            6: (
                "Disable Hands-free Commands"
                if self.app.handsfree and self.app.handsfree.enabled
                else "Enable Hands-free Commands",
                "hands-free",
                c.preferences.backend == "local"
                and (not c.busy or (self.app.handsfree and self.app.handsfree.enabled)),
            ),
            7: ("Copy Uninserted Text", "copy-pending", bool(c.pending_text) and not c.busy),
            8: ("About DictaDesk", "about", True),
            9: ("Usage & Commands…", "usage", True),
            10: (
                "Resume Voice Commands Now"
                if self.app.handsfree and self.app.handsfree.paused
                else "Pause Voice Commands for 30 Minutes",
                "pause-commands",
                bool(self.app.handsfree and self.app.handsfree.enabled and not c.busy),
            ),
            5: ("Quit", "quit", True),
        }

    def props(self, ident):
        if ident == 0:
            return {"children-display": GLib.Variant("s", "submenu")}
        label, action, enabled = self.rows()[ident]
        return {
            "label": GLib.Variant("s", label),
            "enabled": GLib.Variant("b", enabled),
            "visible": GLib.Variant(
                "b", ident not in (6, 10) or self.app.session.preferences.backend == "local"
            ),
        }

    def event(self, ident, event):
        row = self.rows().get(ident)
        if row and event == "clicked" and row[2]:
            self.app.activate_action(row[1], None)

    def menu_call(self, connection, sender, path, interface, method, args, invocation):
        values = args.unpack()
        if method == "GetLayout":
            parent, depth, names = values
            if parent not in (0, *self.rows()):
                invocation.return_dbus_error(
                    "com.canonical.dbusmenu.Error.InvalidMenuItem", "Unknown item"
                )
                return
            children = (
                [GLib.Variant("(ia{sv}av)", (i, self.props(i), [])) for i in self.rows()]
                if parent == 0 and depth != 0
                else []
            )
            reply = GLib.Variant(
                "(u(ia{sv}av))", (self.revision, (parent, self.props(parent), children))
            )
        elif method == "GetGroupProperties":
            ids, names = values
            reply = GLib.Variant(
                "(a(ia{sv}))", ([(i, self.props(i)) for i in ids if i in self.rows()],)
            )
        elif method == "GetProperty":
            ident, prop = values
            reply = GLib.Variant("(v)", (self.props(ident).get(prop, GLib.Variant("s", "")),))
        elif method == "Event":
            self.event(values[0], values[1])
            reply = None
        elif method == "EventGroup":
            for ident, event, data, stamp in values[0]:
                self.event(ident, event)
            reply = GLib.Variant("(ai)", ([],))
        elif method == "AboutToShowGroup":
            reply = GLib.Variant("(aiai)", ([], []))
        else:
            reply = GLib.Variant("(b)", (False,))
        invocation.return_value(reply)

    def menu_property(self, connection, sender, path, interface, prop):
        return GLib.Variant(
            *{
                "Version": ("u", 3),
                "TextDirection": ("s", "ltr"),
                "Status": ("s", "normal"),
                "IconThemePath": ("as", [str(self.resources)]),
            }[prop]
        )

    def update(self, state, title, symbolic):
        self.state, self.title, self.symbolic = state, title, symbolic
        self.revision += 1
        for signal in ("NewIcon", "NewToolTip"):
            self.connection.emit_signal(
                None, "/StatusNotifierItem", "org.kde.StatusNotifierItem", signal, None
            )
        self.connection.emit_signal(
            None,
            "/Menu",
            "com.canonical.dbusmenu",
            "LayoutUpdated",
            GLib.Variant("(ui)", (self.revision, 0)),
        )

    def close(self):
        Gio.bus_unwatch_name(self.watch)
        self.connection.signal_unsubscribe(self.subscription)
        self.connection.signal_unsubscribe(self.host_signal)
        for item in self.items:
            self.connection.unregister_object(item)
