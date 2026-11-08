# SPDX-License-Identifier: GPL-3.0-only
"""Monitor selection does not assume the first output is the primary display."""

from types import SimpleNamespace
from unittest import TestCase
from dictadesk.ui.command_chooser import primary_monitor


def monitor(name, x, y, w=1920, h=1080):
    return SimpleNamespace(
        get_connector=lambda: name,
        get_geometry=lambda: SimpleNamespace(x=x, y=y, width=w, height=h),
    )


class PrimaryMonitorTests(TestCase):
    def test_primary_output_takes_precedence_over_origin(self):
        left, right = monitor("HDMI-1", 0, 0), monitor("DP-1", 1920, 0)
        self.assertIs(primary_monitor([left, right], "DP-1"), right)

    def test_origin_fallback_with_negative_monitor_coordinates(self):
        left, right = monitor("HDMI-1", -1920, 0), monitor("DP-1", 0, 0)
        self.assertIs(primary_monitor([left, right]), right)
        self.assertIs(primary_monitor([left, right], "missing"), right)

    def test_no_origin_uses_first_available_monitor(self):
        item = monitor("HDMI-1", 1920, 1080)
        self.assertIs(primary_monitor([item]), item)
