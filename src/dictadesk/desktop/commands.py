# SPDX-License-Identifier: GPL-3.0-only
"""Allowlisted desktop actions. No recognized speech is executed by a shell."""

import json
import os
import subprocess
import threading
import time
import shutil
from pathlib import Path
from datetime import datetime

from ..ui.gtk import Gio, GLib
from .lock import screen_service

NORMAL_VOLUME = 65536


class DesktopCommands:
    def __init__(
        self, dispatch, *, connection=None, run=subprocess.run, spawn=subprocess.Popen
    ):
        self.dispatch = dispatch
        self.connection = connection
        self.run = run
        self.spawn = spawn
        self.media_recent = {}
        self.media_subscription = None

    def start(self):
        try:

            def changed(connection, sender, path, interface, signal, parameters):
                iface, changes, _ = parameters.unpack()
                if iface == "org.mpris.MediaPlayer2.Player" and any(
                    key in changes for key in ("PlaybackStatus", "Metadata")
                ):
                    self.media_recent[sender] = time.monotonic()

            self.connection = self.connection or Gio.bus_get_sync(Gio.BusType.SESSION, None)
            self.media_subscription = self.connection.signal_subscribe(
                None,
                "org.freedesktop.DBus.Properties",
                "PropertiesChanged",
                "/org/mpris/MediaPlayer2",
                None,
                Gio.DBusSignalFlags.NONE,
                changed,
            )
        except GLib.Error:
            pass

    def close(self):
        if self.media_subscription is not None:
            self.connection.signal_unsubscribe(self.media_subscription)
            self.media_subscription = None

    def execute(self, kind, arguments, valid, done, *, target=None):
        if not valid():
            return
        if kind in ("lock", "screensaver"):
            self.screen(kind, valid, done)
        else:

            def work():
                try:
                    if kind == "window":
                        if os.environ.get("XDG_SESSION_TYPE") == "wayland":
                            raise ValueError("Window control requires X11.")
                        from .window_commands import perform

                        message = perform(arguments[0], target, valid)
                    elif kind == "screen-off":
                        if os.environ.get("XDG_SESSION_TYPE") == "wayland":
                            raise ValueError("Display power control requires X11 DPMS.")
                        from .window_commands import screen_off

                        message = screen_off(valid)
                    elif kind == "suspend":
                        if valid():
                            self.call(
                                "org.freedesktop.login1",
                                "/org/freedesktop/login1",
                                "org.freedesktop.login1.Manager",
                                "Suspend",
                                GLib.Variant("(b)", (True,)),
                                system=True,
                            )
                        message = "System suspend requested."
                    elif kind == "screenshot":
                        message = self.screenshot(arguments, target, valid)
                    else:
                        handlers = {
                            "volume": self.volume,
                            "brightness": self.brightness,
                            "dnd": self.dnd,
                            "media": self.media,
                        }
                        if kind not in handlers:
                            raise ValueError("Unsupported desktop command.")
                        message = handlers[kind](arguments, valid)
                except ValueError as exc:
                    message = str(exc)
                except Exception:
                    message = (
                        "Volume could not be changed. Check the sound output."
                        if kind == "volume"
                        else "The requested desktop action could not be completed. Check its availability in Usage & Commands."
                    )
                self.dispatch(lambda: done(message) if valid() else None)

            threading.Thread(target=work, daemon=True).start()

    def screen(self, kind, valid, done):
        name, path = screen_service()
        method = "Lock" if kind == "lock" else "SetActive"
        arguments = (
            GLib.Variant("(s)", ("",))
            if method == "Lock" and name == "org.cinnamon.ScreenSaver"
            else None
            if method == "Lock"
            else GLib.Variant("(b)", (True,))
        )
        try:
            connection = self.connection or Gio.bus_get_sync(Gio.BusType.SESSION, None)

            def completed(conn, result):
                try:
                    conn.call_finish(result)
                    message = (
                        "Screen lock requested." if kind == "lock" else "Screensaver activated."
                    )
                except GLib.Error:
                    message = "The desktop could not activate the screen lock or screensaver."
                if valid():
                    done(message)

            if valid():
                connection.call(
                    name,
                    path,
                    name,
                    method,
                    arguments,
                    None,
                    Gio.DBusCallFlags.NONE,
                    5000,
                    None,
                    completed,
                )
        except GLib.Error:
            if valid():
                done("The desktop screen lock or screensaver is unavailable.")

    def pactl(self, *arguments):
        # pactl is the supported client for PulseAudio and pipewire-pulse.
        # Fixed argv, bounded output/time and numeric values only; never a shell.
        result = self.run(
            ["pactl", *arguments],
            check=True,
            capture_output=True,
            text=True,
            timeout=3,
            env={**os.environ, "LC_ALL": "C"},
        )
        return result.stdout

    def volume(self, arguments, valid):
        action = arguments[0]
        if action not in ("up", "down", "set", "mute", "unmute"):
            raise ValueError("Unsupported sound action")
        default = self.pactl("get-default-sink").strip()
        sinks = json.loads(self.pactl("--format=json", "list", "sinks"))
        sink = next((sink for sink in sinks if sink["name"] == default), None)
        if sink is None:
            raise ValueError("No default sound output")
        index = int(sink["index"])
        if action in ("mute", "unmute"):
            if valid():
                self.pactl("set-sink-mute", str(index), "1" if action == "mute" else "0")
            return "Volume muted." if action == "mute" else "Volume unmuted."
        amount = arguments[1]
        if type(amount) is not int or not (0 if action == "set" else 1) <= amount <= 100:
            raise ValueError("Invalid volume step")
        delta = round(amount * NORMAL_VOLUME / 100) * (1 if action == "up" else -1)
        values = [
            max(
                0,
                min(
                    NORMAL_VOLUME,
                    round(amount * NORMAL_VOLUME / 100)
                    if action == "set"
                    else int(channel["value"]) + delta,
                ),
            )
            for channel in sink["volume"].values()
        ]
        if not values:
            raise ValueError("No volume channels")
        if valid():
            self.pactl("set-sink-volume", str(index), *(str(value) for value in values))
        return f"Volume {round(max(values) * 100 / NORMAL_VOLUME)}%."

    def bus(self, system=False):
        if not system and self.connection:
            return self.connection
        return Gio.bus_get_sync(Gio.BusType.SYSTEM if system else Gio.BusType.SESSION, None)

    def call(self, name, path, interface, method, arguments=None, *, system=False):
        return (
            self.bus(system)
            .call_sync(
                name,
                path,
                interface,
                method,
                arguments,
                None,
                Gio.DBusCallFlags.NONE,
                5000,
                None,
            )
            .unpack()
        )

    def names(self):
        return self.call(
            "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "ListNames"
        )[0]

    def brightness(self, arguments, valid):
        action, amount = arguments
        if type(amount) is not int or not 0 <= amount <= 100:
            raise ValueError("Invalid brightness percentage.")
        name, path, interface = (
            "org.cinnamon.SettingsDaemon.Power",
            "/org/cinnamon/SettingsDaemon/Power",
            "org.cinnamon.SettingsDaemon.Power.Screen",
        )
        current = self.call(name, path, interface, "GetPercentage")[0]
        if not 0 <= current <= 100:
            raise ValueError("This display does not expose a supported backlight.")
        desired = (
            amount
            if action == "set"
            else max(0, min(100, current + amount * (1 if action == "up" else -1)))
        )
        if valid():
            actual = self.call(
                name, path, interface, "SetPercentage", GLib.Variant("(u)", (desired,))
            )[0]
            if not 0 <= actual <= 100:
                raise ValueError("Brightness could not be changed.")
            return f"Brightness {actual}%."
        return "Cancelled."

    @staticmethod
    def notification_settings():
        source = Gio.SettingsSchemaSource.get_default()
        for schema, key in (
            ("org.cinnamon.desktop.notifications", "display-notifications"),
            ("org.gnome.desktop.notifications", "show-banners"),
        ):
            found = source.lookup(schema, True)
            if found and found.has_key(key):
                settings = Gio.Settings.new_full(found, None, None)
                if settings.is_writable(key):
                    return settings, key
        raise ValueError("Do Not Disturb is unavailable on this desktop.")

    def dnd(self, arguments, valid):
        settings, key = self.notification_settings()
        if valid() and not settings.set_boolean(key, not arguments[0]):
            raise ValueError("Notification settings could not be saved.")
        Gio.Settings.sync()
        return "Do Not Disturb enabled." if arguments[0] else "Do Not Disturb disabled."

    def media(self, arguments, valid):
        method = arguments[0]
        required = {
            "Play": "CanPlay",
            "Pause": "CanPause",
            "PlayPause": "CanControl",
            "Next": "CanGoNext",
            "Previous": "CanGoPrevious",
            "Stop": "CanControl",
        }
        if method not in required:
            raise ValueError("Unsupported media operation.")
        players = []
        for name in self.names():
            if not name.startswith("org.mpris.MediaPlayer2."):
                continue
            try:
                properties = self.call(
                    name,
                    "/org/mpris/MediaPlayer2",
                    "org.freedesktop.DBus.Properties",
                    "GetAll",
                    GLib.Variant("(s)", ("org.mpris.MediaPlayer2.Player",)),
                )[0]
                if properties.get("CanControl", False) and properties.get(
                    required[method], False
                ):
                    owner = self.call(
                        "org.freedesktop.DBus",
                        "/org/freedesktop/DBus",
                        "org.freedesktop.DBus",
                        "GetNameOwner",
                        GLib.Variant("(s)", (name,)),
                    )[0]
                    players.append(
                        (
                            properties.get("PlaybackStatus") == "Playing",
                            max(
                                self.media_recent.get(name, 0), self.media_recent.get(owner, 0)
                            ),
                            name,
                        )
                    )
            except GLib.Error:
                continue
        if not players:
            raise ValueError("No compatible media player can perform this action.")
        name = max(players)[2]
        if valid():
            self.call(name, "/org/mpris/MediaPlayer2", "org.mpris.MediaPlayer2.Player", method)
            self.media_recent[name] = time.monotonic()
        return "Media command sent."

    def screenshot(self, arguments, target, valid):
        # Use the normal desktop tool so its save directory/dialog preferences
        # remain authoritative. It is launched with fixed arguments, never speech.
        tool = shutil.which("gnome-screenshot") or shutil.which("cinnamon-screenshot")
        if not tool and arguments[0] != "window":
            raise ValueError("No supported desktop screenshot tool is installed.")

        def launch(_connection=None):
            if valid():
                self.spawn(
                    [tool] + (["--window"] if arguments[0] == "window" else []),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            return "Screenshot requested using the desktop screenshot tool."

        if arguments[0] == "window":
            if os.environ.get("XDG_SESSION_TYPE") == "wayland":
                raise ValueError("Capturing the remembered application window requires X11.")
            # Direct D-Bus capture completes before focus is returned. The tool's
            # --window mode is only safe while that original window stays active.
            from .window_commands import focused_operation

            def capture(_connection):
                picture = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_PICTURES)
                folder = Path(picture) if picture else Path.home()
                schemas = Gio.SettingsSchemaSource.get_default()
                schema = schemas.lookup("org.gnome.gnome-screenshot", True)
                if schema and schema.has_key("auto-save-directory"):
                    configured = Gio.Settings.new_full(schema, None, None).get_string(
                        "auto-save-directory"
                    )
                    if configured:
                        file = (
                            Gio.File.new_for_uri(configured)
                            if configured.startswith("file:")
                            else Gio.File.new_for_path(configured)
                        )
                        path = file.get_path()
                        if path:
                            folder = Path(path)
                folder.mkdir(parents=True, exist_ok=True)
                filename = folder / (
                    "Screenshot from "
                    + datetime.now().strftime("%Y-%m-%d %H-%M-%S-%f")
                    + ".png"
                )
                if not valid():
                    return "Cancelled."
                success, used = self.call(
                    "org.gnome.Shell.Screenshot",
                    "/org/gnome/Shell/Screenshot",
                    "org.gnome.Shell.Screenshot",
                    "ScreenshotWindow",
                    GLib.Variant("(bbbs)", (True, False, True, str(filename))),
                )
                if not success:
                    raise ValueError("The desktop could not capture the original window.")
                return "Screenshot saved to " + used

            return focused_operation(target, valid, capture)
        return launch()

    def probe(self):
        """Read-only availability for help. Failures stay visible, never succeed silently."""
        result = {"applications": "", "clipboard": "", "format": "", "commands": ""}
        try:
            names = self.names()
        except GLib.Error:
            names = []
        result["audio"] = (
            "" if shutil.which("pactl") else "PulseAudio/PipeWire control utility is missing."
        )
        result["media"] = (
            ""
            if any(name.startswith("org.mpris.MediaPlayer2.") for name in names)
            else "No MPRIS player is currently running."
        )
        result["screen"] = (
            "Desktop screensaver service is required; availability is checked on use."
        )
        try:
            activatable = self.call(
                "org.freedesktop.DBus",
                "/org/freedesktop/DBus",
                "org.freedesktop.DBus",
                "ListActivatableNames",
            )[0]
            if screen_service()[0] in list(names) + list(activatable):
                result["screen"] = ""
        except GLib.Error:
            pass
        try:
            value = self.call(
                "org.cinnamon.SettingsDaemon.Power",
                "/org/cinnamon/SettingsDaemon/Power",
                "org.cinnamon.SettingsDaemon.Power.Screen",
                "GetPercentage",
            )[0]
            result["brightness"] = (
                "" if 0 <= value <= 100 else "No supported display backlight."
            )
        except GLib.Error:
            result["brightness"] = "No supported display backlight service."
        try:
            self.notification_settings()
            result["dnd"] = ""
        except ValueError as exc:
            result["dnd"] = str(exc)
        try:
            status = self.call(
                "org.freedesktop.login1",
                "/org/freedesktop/login1",
                "org.freedesktop.login1.Manager",
                "CanSuspend",
                system=True,
            )[0]
            result["suspend"] = (
                ""
                if status in ("yes", "challenge")
                else "System suspend is unavailable or disallowed."
            )
        except GLib.Error:
            result["suspend"] = "System suspend service is unavailable."
        x11 = os.environ.get("XDG_SESSION_TYPE") != "wayland" and bool(
            os.environ.get("DISPLAY")
        )
        result["window"] = result["paste"] = "" if x11 else "Requires an X11 session."
        result["dpms"] = "Requires X11 DPMS."
        if x11:
            try:
                from contextlib import closing
                from Xlib import display
                from .window_commands import prop

                with closing(display.Display()) as connection:
                    if not prop(connection, connection.screen().root, "_NET_SUPPORTED"):
                        result["window"] = "No compatible EWMH window manager is available."
                    if connection.has_extension("DPMS") and connection.dpms_capable().capable:
                        result["dpms"] = (
                            ""
                            if connection.dpms_info().state
                            else "DPMS is disabled in the desktop settings."
                        )
                    else:
                        result["dpms"] = "This display does not support X11 DPMS."
            except Exception:
                result["window"] = result["dpms"] = "X11 display control is unavailable."
        result["screenshot"] = (
            ""
            if shutil.which("gnome-screenshot") or shutil.which("cinnamon-screenshot")
            else "Desktop screenshot tool is unavailable."
        )
        result["screenshot-window"] = (
            ""
            if x11 and "org.gnome.Shell.Screenshot" in names
            else "Requires X11 and the Cinnamon-compatible screenshot service."
        )
        return result
