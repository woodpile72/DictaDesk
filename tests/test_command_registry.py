# SPDX-License-Identifier: GPL-3.0-only
import unittest
from dataclasses import replace
from unittest.mock import Mock, patch
from types import SimpleNamespace

from dictadesk.core.commands import REGISTRY, registry, recognize, CATEGORIES
from dictadesk.core.settings import Preferences
from dictadesk.core.transcript import Transcript, punctuate
from dictadesk.desktop.commands import DesktopCommands, NORMAL_VOLUME
from dictadesk.ui.usage import reference_rows


class RegistryTests(unittest.TestCase):
    def test_identifiers_examples_and_help_are_one_registry(self):
        specs = registry()
        self.assertEqual(len({s.ident for s in specs}), len(specs))
        self.assertEqual({s.category for s in specs}, set(CATEGORIES))
        rows = reference_rows(Preferences(), {s.capability: "" for s in specs})
        self.assertEqual([row[0] for row in rows], list(specs))
        for spec in REGISTRY:
            for example in spec.examples or spec.phrases:
                if (
                    spec.parameter == "application"
                    or spec.parameter == "percentage"
                    and not spec.examples
                ):
                    continue
                self.assertEqual(recognize(example)[0], spec.handler, example)
        rows = reference_rows(replace(Preferences(), volume_step=7, brightness_step=9), {})
        self.assertIn("7 percentage points", rows[0][2])

    def test_new_controls_and_numeric_variations(self):
        for text, expected in {
            "volume fifty percent": ("volume", ("set", 50)),
            "volume 0%": ("volume", ("set", 0)),
            "VOLUME MAX!": ("volume", ("set", 100)),
            "brightness up twenty percent": ("brightness", ("up", 20)),
            "brightness 70": ("brightness", ("set", 70)),
            "brightness down by ten": ("brightness", ("down", 10)),
            "screen off": ("screen-off", ()),
            "suspend computer": ("suspend", ()),
            "do not disturb on": ("dnd", (True,)),
            "do not disturb off": ("dnd", (False,)),
            "play pause": ("media", ("PlayPause",)),
            "next track": ("media", ("Next",)),
            "take screenshot": ("screenshot", ("screen",)),
            "screenshot window": ("screenshot", ("window",)),
            "close window": ("window", ("close",)),
            "copy that": ("clipboard", ("copy",)),
            "paste that": ("clipboard", ("paste",)),
            "clear clipboard": ("clipboard", ("clear",)),
            "pause voice commands": ("pause-commands", (30,)),
            "suspend voice commands for half an hour": ("pause-commands", (30,)),
            "pause voice commands for fifteen minutes": ("pause-commands", (15,)),
        }.items():
            self.assertEqual(recognize(text), expected, text)

    def test_configured_defaults_and_validation(self):
        prefs = replace(Preferences(), volume_step=7, brightness_step=9).validate()
        self.assertEqual(recognize("volume up", prefs), ("volume", ("up", 7)))
        self.assertEqual(recognize("brightness down", prefs), ("brightness", ("down", 9)))
        self.assertEqual(recognize("volume down ten", prefs), ("volume", ("down", 10)))
        for key in ("volume_step", "brightness_step"):
            for value in (0, 101, True, "five"):
                with self.assertRaises(ValueError):
                    replace(prefs, **{key: value}).validate()

    def test_unknown_or_formatting_never_matches_a_system_command(self):
        for text in (
            "mic mute",
            "mic unmute",
            "microphone mute",
            "volume-15",
            "new paragraph",
            "tab",
            "period",
            "sudo reboot",
            "volume 101",
            "brightness -10",
            "volume 1.5%",
            "please close window",
            "play then suspend",
            "lock screen and close window",
        ):
            self.assertEqual(recognize(text), ("unknown", ()), text)

    def test_dictation_formats_tab_and_keeps_system_commands_as_text(self):
        self.assertEqual(punctuate("one tab two new line three period"), "one\ttwo\nThree.")
        self.assertEqual(punctuate("Hello comma, world period."), "Hello, world.")
        transcript = Transcript()
        transcript.replace("volume max and lock screen", [], True)
        self.assertEqual(transcript.text, "volume max and lock screen")


class AdditionalAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = DesktopCommands(lambda callback: callback(), run=Mock())

    def test_absolute_volume_does_not_add_to_previous_volume(self):
        self.adapter.pactl = Mock(
            side_effect=[
                "output",
                '[{"name":"output", "index":3, "volume":{"left":{"value":60000},"right":{"value":10000}}}]',
                "",
            ]
        )
        self.adapter.volume(("set", 50), lambda: True)
        self.assertEqual(
            self.adapter.pactl.call_args.args,
            ("set-sink-volume", "3", str(NORMAL_VOLUME // 2), str(NORMAL_VOLUME // 2)),
        )

    def test_brightness_uses_real_percentage_and_clamps(self):
        self.adapter.call = Mock(side_effect=[(95,), (100,)])
        self.assertEqual(self.adapter.brightness(("up", 20), lambda: True), "Brightness 100%.")
        self.assertEqual(self.adapter.call.call_args.args[-1].unpack(), (100,))
        self.adapter.call = Mock(side_effect=[(50,), (20,)])
        self.adapter.brightness(("set", 20), lambda: True)
        self.assertEqual(self.adapter.call.call_args.args[-1].unpack(), (20,))

    def test_unsupported_brightness_and_stale_actions_never_report_success(self):
        self.adapter.call = Mock(return_value=(0xFFFFFFFF,))
        with self.assertRaises(ValueError):
            self.adapter.brightness(("up", 5), lambda: True)
        self.assertEqual(self.adapter.call.call_count, 1)
        self.adapter.call = Mock(return_value=(50,))
        self.adapter.brightness(("set", 20), lambda: False)
        self.assertEqual(self.adapter.call.call_count, 1)

    def test_media_prefers_playing_then_recent_and_handles_no_player(self):
        self.adapter.names = lambda: ["org.mpris.MediaPlayer2.a", "org.mpris.MediaPlayer2.b"]

        def call(name, path, iface, method, arguments=None, **kwargs):
            if method == "GetAll":
                return (
                    {
                        "CanControl": True,
                        "CanPlay": True,
                        "PlaybackStatus": "Playing" if name.endswith(".a") else "Paused",
                    },
                )
            if method == "GetNameOwner":
                return (arguments.unpack()[0],)
            return ()

        self.adapter.call = Mock(side_effect=call)
        self.adapter.media_recent["org.mpris.MediaPlayer2.b"] = 99999999999
        self.adapter.media(("Play",), lambda: True)
        self.assertEqual(self.adapter.call.call_args.args[0], "org.mpris.MediaPlayer2.a")
        self.adapter.names = lambda: []
        with self.assertRaisesRegex(ValueError, "No compatible"):
            self.adapter.media(("Play",), lambda: True)

    def test_dnd_respects_schema_and_write_failure(self):
        settings = Mock()
        with (
            patch.object(
                self.adapter,
                "notification_settings",
                return_value=(settings, "display-notifications"),
            ),
            patch("dictadesk.desktop.commands.Gio.Settings.sync"),
        ):
            self.adapter.dnd((True,), lambda: True)
            settings.set_boolean.assert_called_once_with("display-notifications", False)
            settings.set_boolean.return_value = False
            with self.assertRaises(ValueError):
                self.adapter.dnd((False,), lambda: True)

    def test_screenshot_unsupported_fails_without_spawning(self):
        self.adapter.spawn = Mock()
        with patch("dictadesk.desktop.commands.shutil.which", return_value=None):
            with self.assertRaises(ValueError):
                self.adapter.screenshot(("screen",), SimpleNamespace(window=None), lambda: True)
        self.adapter.spawn.assert_not_called()
