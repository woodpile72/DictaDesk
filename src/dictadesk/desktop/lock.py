# SPDX-License-Identifier: GPL-3.0-only
"""Observe desktop screen locking without ever activating or unlocking it."""

import os

from ..ui.gtk import Gio, GLib

SERVICES = (
    ("org.cinnamon.ScreenSaver", "/org/cinnamon/ScreenSaver"),
    ("org.gnome.ScreenSaver", "/org/gnome/ScreenSaver"),
    ("org.freedesktop.ScreenSaver", "/org/freedesktop/ScreenSaver"),
)


def screen_service():
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").casefold()
    index = 0 if "cinnamon" in desktop else 1 if "gnome" in desktop else 2
    return SERVICES[index]


class ScreenLock:
    def __init__(self, changed, *, connection=None, services=SERVICES):
        self.changed = changed
        self.connection = connection or Gio.bus_get_sync(Gio.BusType.SESSION, None)
        if services is SERVICES:
            services = (screen_service(),)
        self.paths = dict(services)
        self.owners = {}
        self.states = {}
        self.revisions = {}
        self.subscriptions = {}
        self.lost = set()
        self.closed = False
        self.last = None
        self.watches = []
        self.publish()
        for name in self.paths:
            self.watches.append(
                Gio.bus_watch_name_on_connection(
                    self.connection,
                    name,
                    Gio.BusNameWatcherFlags.NONE,
                    self.appeared,
                    self.vanished,
                )
            )
        self.retry = GLib.timeout_add_seconds(5, self.retry_unknown)

    def publish(self):
        if self.closed:
            return
        present = [name for name, owner in self.owners.items() if owner]
        locked = any(self.states.get(name) is True for name in present)
        # Cinnamon intentionally exits 30 seconds after becoming inactive.
        # Its last confirmed inactive state remains valid on normal exit.
        inactive_cinnamon = (
            "org.cinnamon.ScreenSaver" in self.paths
            and self.states.get("org.cinnamon.ScreenSaver") is False
        )
        unknown = (
            len(self.owners) != len(self.paths)
            or (not present and not inactive_cinnamon)
            or bool(self.lost)
            or any(self.states.get(name) is None for name in present)
        )
        status = (
            locked or unknown,
            "Screen locked" if locked else "Desktop lock status unavailable" if unknown else "",
        )
        if status != self.last:
            self.last = status
            self.changed(*status)

    def appeared(self, connection, name, owner):
        if self.closed:
            return
        self.unsubscribe(name)
        self.owners[name] = owner
        self.states[name] = None
        self.revisions[name] = self.revisions.get(name, 0) + 1
        self.publish()

        def active_changed(conn, sender, path, interface, signal, args):
            if self.closed or self.owners.get(name) != sender:
                return
            self.revisions[name] += 1
            self.states[name] = bool(args.unpack()[0])
            self.lost.discard(name)
            self.publish()

        self.subscriptions[name] = connection.signal_subscribe(
            owner,
            name,
            "ActiveChanged",
            self.paths[name],
            None,
            Gio.DBusSignalFlags.NONE,
            active_changed,
        )
        self.query(name)

    def query(self, name, *, activate=False):
        owner, revision = self.owners[name], self.revisions[name]

        def received(connection, result):
            if (
                self.closed
                or self.owners.get(name) != owner
                or self.revisions.get(name) != revision
            ):
                return
            try:
                self.states[name] = bool(connection.call_finish(result).unpack()[0])
                self.lost.discard(name)
            except GLib.Error:
                self.states[name] = None
            self.publish()

        self.connection.call(
            owner or name,
            self.paths[name],
            name,
            "GetActive",
            None,
            GLib.VariantType.new("(b)"),
            Gio.DBusCallFlags.NONE if activate else Gio.DBusCallFlags.NO_AUTO_START,
            3000,
            None,
            received,
        )

    def vanished(self, connection, name):
        if self.closed:
            return
        normal_exit = name == "org.cinnamon.ScreenSaver" and self.states.get(name) is False
        if self.owners.get(name) and self.states.get(name) is True:
            self.lost.add(name)
        self.unsubscribe(name)
        self.owners[name] = None
        self.states[name] = False if normal_exit else None
        self.revisions[name] = self.revisions.get(name, 0) + 1
        self.publish()
        if (
            name == "org.cinnamon.ScreenSaver"
            and self.states[name] is None
            and name not in self.lost
        ):
            # Read-only startup probe; never call Lock, SetActive or Unlock.
            self.query(name, activate=True)

    def retry_unknown(self):
        if self.closed:
            return False
        for name, owner in self.owners.items():
            if owner and self.states.get(name) is None:
                self.query(name)
            elif (
                name == "org.cinnamon.ScreenSaver"
                and self.states.get(name) is None
                and name not in self.lost
            ):
                self.query(name, activate=True)
        return True

    def unsubscribe(self, name):
        subscription = self.subscriptions.pop(name, None)
        if subscription is not None:
            self.connection.signal_unsubscribe(subscription)

    def close(self):
        if self.closed:
            return
        self.closed = True
        for name in list(self.subscriptions):
            self.unsubscribe(name)
        for watch in self.watches:
            Gio.bus_unwatch_name(watch)
        if self.retry:
            GLib.source_remove(self.retry)
            self.retry = None
