# SPDX-License-Identifier: GPL-3.0-only
"""Timer grammar, durable state and sound cancellation without real devices."""

import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock

from dictadesk.core.countdown import Countdown, describe
from dictadesk.core.voice_commands import command, wake_activation
from dictadesk.audio.alarm import Alarm


class TimerTests(unittest.TestCase):
    def test_numeric_and_spoken_duration_commands(self):
        for phrase, seconds in (
            ("24 minutes", 1440),
            ("twenty-four minutes", 1440),
            ("one hour and thirty minutes", 5400),
            ("a minute", 60),
            ("half an hour", 1800),
            ("90 seconds", 90),
            ("one hundred and twenty minutes", 7200),
            ("24 hours", 86400),
            ("1 hour 2 minutes 3 seconds", 3723),
        ):
            for prefix in ("set a timer for ", "set timer ", "start a timer for "):
                text = prefix + phrase + "."
                self.assertEqual(command(text), ("timer", ("set", seconds)))
                _, suffix = wake_activation("Computer " + text, "Computer")
                self.assertEqual(command(suffix), ("timer", ("set", seconds)))
        for text in (
            "set a timer",
            "set a timer for 24",
            "set a timer for -24 minutes",
            "set a timer for 1.5 minutes",
            "set a timer for 0 seconds",
            "set a timer for 25 hours",
            "set a timer for 24% minutes",
            "set a timer for 3 minutes and lock screen",
            "set a timer for 1 minute 2 minutes",
            "set a timer for one hour and and two minutes",
        ):
            self.assertEqual(command(text), ("unknown", ()), text)
        for phrase in ("cancel timer", "stop the timer", "stop alarm"):
            self.assertEqual(command(phrase), ("timer", ("cancel",)))
        self.assertEqual(command("How much time is left?"), ("timer", ("status",)))

    def test_persistence_suspend_clock_adjustment_and_expiry_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "timer.json"
            boot, wall = [50], [1000]
            timer = Countdown(path, clock=lambda: boot[0], wall=lambda: wall[0])
            timer.start(1440)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(ValueError):
                timer.start(30)
            wall[0] += 5000  # Clock correction cannot expire the running timer.
            self.assertEqual(timer.remaining(), 1440)
            boot[0] += 1440  # Includes time spent suspended on Linux.
            self.assertTrue(timer.poll())
            self.assertFalse(timer.poll())
            restored = Countdown(path, clock=lambda: boot[0], wall=lambda: wall[0])
            self.assertTrue(restored.elapsed)
            self.assertFalse(restored.poll())
            restored.start(60)
            wall[0] += 30
            boot[0] += 30
            restored = Countdown(path, clock=lambda: boot[0], wall=lambda: wall[0])
            self.assertEqual(restored.remaining(), 30)
            restored.cancel()
            self.assertFalse(Countdown(path).exists)
            self.assertEqual(describe(61), "1 minute 1 second")

    def test_overdue_restart_and_corrupt_state_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "timer.json"
            timer = Countdown(path, clock=lambda: 10, wall=lambda: 1000)
            timer.start(1)
            restored = Countdown(path, clock=lambda: 50, wall=lambda: 1100, boot_id="rebooted")
            self.assertTrue(restored.poll())
            path.write_text(json.dumps({"seconds": 1, "end": float("nan"), "elapsed": False}))
            damaged = Countdown(path)
            self.assertTrue(damaged.problem)
            with self.assertRaises(ValueError):
                damaged.start(30)
            damaged.cancel()
            damaged.start(30)
            self.assertTrue(damaged.active)

    def test_alarm_cancels_and_waits_for_output_to_close(self):
        entered, closed = threading.Event(), threading.Event()

        def output(rate, data, stop):
            self.assertEqual(rate, 16000)
            self.assertTrue(any(data))
            entered.set()
            stop.wait(2)
            closed.set()

        alarm = Alarm(output)
        self.assertTrue(alarm.start())
        self.assertTrue(entered.wait(2))
        self.assertFalse(alarm.start())
        alarm.stop()
        alarm.wait(threading.Event())
        self.assertTrue(closed.is_set())
        self.assertFalse(alarm.busy)
        failed = Alarm(Mock(side_effect=RuntimeError("fixture")))
        failed.start()
        failed.wait(threading.Event())
        self.assertIn("could not be played", failed.problem)
