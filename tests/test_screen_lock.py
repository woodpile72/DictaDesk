# SPDX-License-Identifier: GPL-3.0-only
from contextlib import ExitStack
import unittest
from unittest.mock import Mock, patch

from dictadesk.desktop.lock import ScreenLock, SERVICES
from dictadesk.ui.gtk import GLib


class ScreenLockTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for name in (
            "Gio.bus_watch_name_on_connection",
            "Gio.bus_unwatch_name",
            "GLib.timeout_add_seconds",
            "GLib.source_remove",
        ):
            self.stack.enter_context(patch("dictadesk.desktop.lock." + name, return_value=1))
        self.connection = Mock()
        self.changed = Mock()
        self.monitor = ScreenLock(
            self.changed, connection=self.connection, services=SERVICES[:1]
        )
        self.addCleanup(self.monitor.close)
        self.name, self.path = SERVICES[0]

    def appear(self):
        self.monitor.appeared(self.connection, self.name, ":1.20")
        self.reply = self.connection.call.call_args.args[-1]
        self.signal = self.connection.signal_subscribe.call_args.args[-1]

    def active(self, value):
        self.signal(
            self.connection,
            ":1.20",
            self.path,
            self.name,
            "ActiveChanged",
            GLib.Variant("(b)", (value,)),
        )

    def test_starts_blocked_and_stale_query_cannot_override_lock(self):
        self.assertTrue(self.changed.call_args.args[0])
        self.appear()
        self.active(True)
        self.connection.call_finish.return_value = GLib.Variant("(b)", (False,))
        self.reply(self.connection, Mock())
        self.assertEqual(self.changed.call_args.args, (True, "Screen locked"))
        self.active(False)
        self.assertEqual(self.changed.call_args.args, (False, ""))

    def test_locked_service_loss_stays_blocked_until_new_confirmed_state(self):
        self.appear()
        self.active(True)
        self.monitor.vanished(self.connection, self.name)
        self.assertTrue(self.changed.call_args.args[0])
        self.active(False)  # signal from departed owner is invalid
        self.assertTrue(self.changed.call_args.args[0])
        self.appear()
        self.active(False)
        self.assertEqual(self.changed.call_args.args, (False, ""))

    def test_confirmed_inactive_cinnamon_may_exit_normally(self):
        self.appear()
        self.active(False)
        self.monitor.vanished(self.connection, self.name)
        self.assertEqual(self.changed.call_args.args, (False, ""))
        self.assertFalse(self.monitor.lost)

    def test_startup_probe_only_reads_and_errors_remain_blocked(self):
        self.monitor.vanished(self.connection, self.name)
        args = self.connection.call.call_args.args
        self.assertEqual(args[3], "GetActive")
        self.connection.call_finish.side_effect = GLib.Error("unavailable")
        args[-1](self.connection, Mock())
        self.assertTrue(self.changed.call_args.args[0])
        self.monitor.retry_unknown()
        self.assertEqual(self.connection.call.call_args.args[3], "GetActive")

    def test_unconfirmed_cinnamon_exit_retries_without_latching_a_lock(self):
        self.appear()
        self.monitor.vanished(self.connection, self.name)
        self.assertFalse(self.monitor.lost)
        self.assertTrue(self.changed.call_args.args[0])
        self.assertEqual(self.connection.call.call_args.args[0], self.name)
        reply = self.connection.call.call_args.args[-1]
        self.connection.call_finish.return_value = GLib.Variant("(b)", (False,))
        reply(self.connection, Mock())
        self.assertEqual(self.changed.call_args.args, (False, ""))

    def test_closed_monitor_ignores_late_results(self):
        self.appear()
        self.monitor.close()
        self.changed.reset_mock()
        self.reply(self.connection, Mock())
        self.active(False)
        self.changed.assert_not_called()
