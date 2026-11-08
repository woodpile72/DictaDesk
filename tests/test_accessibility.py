# SPDX-License-Identifier: GPL-3.0-only
import os
import unittest
from unittest.mock import Mock, patch

from dictadesk.desktop.accessibility import prepare_accessibility, UNAVAILABLE
from dictadesk.desktop.destination import DestinationTracker
from dictadesk.ui.gtk import GLib


class AccessibilityStartupTests(unittest.TestCase):
    def test_verified_session_address_overrides_a_stale_x11_address(self):
        bus, connection = Mock(), Mock()
        bus.call_sync.return_value = GLib.Variant("(s)", ("unix:path=/user/a11y",))
        with (
            patch.dict(os.environ, {"AT_SPI_BUS_ADDRESS": "unix:path=/stale"}),
            patch("dictadesk.desktop.accessibility.Gio.bus_get_sync", return_value=bus),
            patch(
                "dictadesk.desktop.accessibility.Gio.DBusConnection.new_for_address_sync",
                return_value=connection,
            ),
        ):
            self.assertEqual(prepare_accessibility(), "")
            self.assertEqual(os.environ["AT_SPI_BUS_ADDRESS"], "unix:path=/user/a11y")
            connection.close_sync.assert_called_once()

    def test_missing_service_keeps_gtk_and_clipboard_recovery_available(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch(
                "dictadesk.desktop.accessibility.Gio.bus_get_sync",
                side_effect=GLib.Error("missing service"),
            ),
        ):
            self.assertEqual(prepare_accessibility(), UNAVAILABLE)
            self.assertEqual(os.environ["GTK_A11Y"], "none")

    def test_missing_advertised_socket_uses_the_same_safe_fallback(self):
        bus = Mock()
        bus.call_sync.return_value = GLib.Variant("(s)", ("unix:path=/missing",))
        with (
            patch.dict(os.environ, {}),
            patch("dictadesk.desktop.accessibility.Gio.bus_get_sync", return_value=bus),
            patch(
                "dictadesk.desktop.accessibility.Gio.DBusConnection.new_for_address_sync",
                side_effect=GLib.Error("missing socket"),
            ),
        ):
            self.assertEqual(prepare_accessibility(), UNAVAILABLE)
            self.assertEqual(os.environ["GTK_A11Y"], "none")

    def test_disabled_tracker_never_initializes_the_unavailable_native_bridge(self):
        tracker = DestinationTracker(Mock(), accessibility=False)
        self.assertIsNone(tracker.api)
        self.assertIsNone(tracker.listener)


class NativeStartupShutdownTests(unittest.TestCase):
    def test_shutdown_waits_for_inflight_native_microphone_check(self):
        import queue
        import tempfile
        import threading
        import time
        from pathlib import Path
        from dictadesk.core.session import Session
        from dictadesk.core.settings import SettingsStore

        callbacks = queue.Queue()
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        recognizer = Mock()
        with (
            tempfile.TemporaryDirectory() as folder,
            patch.dict(os.environ, {"XDG_CACHE_HOME": folder}),
            patch("dictadesk.core.session.microphones", return_value=[]),
            patch(
                "dictadesk.core.session.check",
                side_effect=lambda _: (entered.set(), release.wait(3)),
            ),
        ):
            session = Session(
                callbacks.put, Mock(), Mock(), SettingsStore(Path(folder) / "prefs"), recognizer
            )
            session.scan_devices()
            self.assertTrue(entered.wait(1))
            session.close(finished.set)
            time.sleep(0.05)
            recognizer.close.assert_not_called()
            self.assertFalse(finished.is_set())
            release.set()
            deadline = time.monotonic() + 2
            while not finished.is_set() and time.monotonic() < deadline:
                try:
                    callbacks.get(timeout=0.05)()
                except queue.Empty:
                    pass
            self.assertTrue(finished.is_set())
            recognizer.close.assert_called_once()
