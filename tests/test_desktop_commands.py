# SPDX-License-Identifier: GPL-3.0-only
"""Desktop action grammar and adapters; never touch the host sound/lock state."""

import json
import subprocess
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from dictadesk.core.voice_commands import command
from dictadesk.desktop.commands import DesktopCommands, NORMAL_VOLUME
from dictadesk.ui.gtk import GLib


class GrammarTests(unittest.TestCase):
    def test_exact_screen_and_volume_commands(self):
        cases = {
            "Lock screen!": ("lock", ()),
            "Screensaver.": ("screensaver", ()),
            "screen saver": ("screensaver", ()),
            "Volume up": ("volume", ("up", 5)),
            "volume down": ("volume", ("down", 5)),
            "volume mute": ("volume", ("mute",)),
            "volume unmute": ("volume", ("unmute",)),
            "volume up 15%": ("volume", ("up", 15)),
            "volume down 50 percent": ("volume", ("down", 50)),
            "volume up by fifteen percent": ("volume", ("up", 15)),
            "volume down fifty": ("volume", ("down", 50)),
            "volume up twenty-five percentage points": ("volume", ("up", 25)),
            "volume down one hundred percent": ("volume", ("down", 100)),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(command(text), expected)

    def test_invalid_amounts_or_extra_words_never_trigger_an_action(self):
        for text in (
            "lock screen and quit",
            "please lock screen",
            "screensaver off",
            "volume up -15%",
            "volume up +15",
            "volume up 1.5%",
            "volume up 0",
            "volume up 101",
            "volume down 99999999999999999999999999999",
            "volume down percent",
            "volume up by",
            "volume mute 20",
            "volume up 15 and open terminal",
            "volume up fifteen fifty",
        ):
            self.assertEqual(command(text), ("unknown", ()), text)


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.run = Mock()
        self.adapter = DesktopCommands(lambda callback: callback(), run=self.run)
        self.sink = {
            "name": "output",
            "index": 7,
            "volume": {
                "front-left": {"value": NORMAL_VOLUME // 2},
                "front-right": {"value": NORMAL_VOLUME // 4},
            },
        }

    def responses(self):
        self.run.side_effect = [
            SimpleNamespace(stdout="output\n"),
            SimpleNamespace(stdout=json.dumps([self.sink])),
            SimpleNamespace(stdout=""),
        ]

    def test_relative_points_preserve_channels_and_use_resolved_sink(self):
        self.responses()
        self.adapter.volume(("up", 15), lambda: True)
        args, kwargs = self.run.call_args
        self.assertEqual(args[0], ["pactl", "set-sink-volume", "7", "42598", "26214"])
        self.assertEqual(kwargs["timeout"], 3)
        self.assertTrue(kwargs["check"])
        self.assertNotIn("shell", kwargs)

    def test_volume_caps_at_zero_and_one_hundred(self):
        for direction, expected in (("up", NORMAL_VOLUME), ("down", 0)):
            self.responses()
            self.adapter.volume((direction, 100), lambda: True)
            self.assertEqual(self.run.call_args.args[0][-2:], [str(expected)] * 2)

    def test_mute_is_idempotent_and_unmute_is_explicit(self):
        for action, value in (("mute", "1"), ("unmute", "0")):
            self.responses()
            self.adapter.volume((action,), lambda: True)
            self.assertEqual(self.run.call_args.args[0], ["pactl", "set-sink-mute", "7", value])

    def test_late_cancel_does_not_modify_sound_output(self):
        self.responses()
        self.adapter.volume(("up", 5), lambda: False)
        self.assertEqual(self.run.call_count, 2)

    def test_missing_sink_and_invalid_amount_fail_without_mutation(self):
        for args in (("up", 101), ("down", "15"), ("execute",)):
            self.responses()
            with self.assertRaises(ValueError):
                self.adapter.volume(args, lambda: True)
            self.assertFalse(
                any(c.args[0][1].startswith("set-") for c in self.run.call_args_list)
            )
            self.run.reset_mock()

    def test_missing_pactl_reports_failure_on_dispatch(self):
        finished = threading.Event()
        feedback = []
        self.run.side_effect = FileNotFoundError("pactl")
        self.adapter.execute(
            "volume",
            ("up", 5),
            lambda: True,
            lambda text: (feedback.append(text), finished.set()),
        )
        self.assertTrue(finished.wait(2))
        self.assertIn("could not be changed", feedback[0])

    def test_timed_out_server_does_not_hang_command_completion(self):
        finished = threading.Event()
        self.run.side_effect = subprocess.TimeoutExpired("pactl", 3)
        self.adapter.execute("volume", ("mute",), lambda: True, lambda _: finished.set())
        self.assertTrue(finished.wait(2))

    def test_screen_calls_supported_dbus_methods_and_never_unlocks(self):
        for desktop, interface, expected_lock in (
            ("X-Cinnamon", "org.cinnamon.ScreenSaver", ("",)),
            ("GNOME", "org.gnome.ScreenSaver", None),
            ("other", "org.freedesktop.ScreenSaver", None),
        ):
            for kind in ("lock", "screensaver"):
                bus, done = Mock(), Mock()
                adapter = DesktopCommands(lambda callback: callback(), connection=bus)
                with patch.dict("os.environ", {"XDG_CURRENT_DESKTOP": desktop}):
                    adapter.execute(kind, (), lambda: True, done)
                args = bus.call.call_args.args
                self.assertEqual(args[0], interface)
                self.assertEqual(args[3], "Lock" if kind == "lock" else "SetActive")
                self.assertEqual(
                    args[4].unpack() if args[4] else None,
                    expected_lock if kind == "lock" else (True,),
                )
                args[-1](bus, Mock())
                done.assert_called_once()

    def test_invalidated_command_never_calls_desktop(self):
        bus, done = Mock(), Mock()
        adapter = DesktopCommands(lambda callback: callback(), connection=bus)
        adapter.execute("lock", (), lambda: False, done)
        bus.call.assert_not_called()
        done.assert_not_called()

    def test_screen_failure_reports_without_claiming_success(self):
        bus, done = Mock(), Mock()
        bus.call_finish.side_effect = GLib.Error("unavailable")
        adapter = DesktopCommands(lambda callback: callback(), connection=bus)
        adapter.execute("screensaver", (), lambda: True, done)
        bus.call.call_args.args[-1](bus, Mock())
        self.assertIn("could not activate", done.call_args.args[0])
