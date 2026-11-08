# SPDX-License-Identifier: GPL-3.0-only
"""Safety boundaries for recorded destinations, including stale remote objects."""

from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import weakref

from dictadesk.desktop.destination import DestinationTracker, Target
from dictadesk.desktop.delivery import Destination


def accessible(path):
    return SimpleNamespace(app=SimpleNamespace(bus_name=":1.42"), path=path)


class DestinationTests(unittest.TestCase):
    def setUp(self):
        self.tracker = DestinationTracker.__new__(DestinationTracker)
        self.tracker.connection = Mock()
        self.tracker.api = Mock()
        self.tracker.targets = weakref.WeakSet()
        self.tracker.focused = None
        self.control, self.document = accessible("/field"), accessible("/document")
        self.window = Destination(100, 101, 42)
        self.target = Target(
            self.window, self.control, (self.control, self.document), (3, ()), b"original"
        )
        self.tracker.targets.add(self.target)
        self.tracker.connection.create_resource_object.return_value.get_full_property.return_value = SimpleNamespace(
            value=[42]
        )
        self.tracker.parents = Mock(return_value=self.target.ancestry)
        self.tracker.usable = Mock(return_value=True)
        self.tracker.states = Mock()
        self.tracker.states.return_value.contains.return_value = False
        self.tracker.snapshot = Mock(return_value=(b"original", (3, ())))

    def test_window_focus_alone_cannot_authorize_automatic_insertion(self):
        target = Target(self.window)
        with patch("dictadesk.desktop.destination.destination", return_value=self.window):
            self.assertFalse(self.tracker.valid(target))
            self.assertFalse(self.tracker.restore(target))

    def test_explicit_recovery_is_bound_to_current_window_without_activation(self):
        target = Target(self.window, explicit=True)
        self.tracker.activate = Mock()
        with patch("dictadesk.desktop.destination.destination", return_value=self.window):
            self.assertTrue(self.tracker.restore(target))
        with patch(
            "dictadesk.desktop.destination.destination", return_value=Destination(200, 201, 42)
        ):
            self.assertFalse(self.tracker.restore(target))
        self.tracker.activate.assert_not_called()

    def test_validity_does_not_depend_on_the_newly_focused_window(self):
        with patch(
            "dictadesk.desktop.destination.destination", return_value=Destination(200, 201, 99)
        ):
            self.assertTrue(self.tracker.valid(self.target))
            self.assertFalse(self.tracker.focused_on(self.target))

    def test_changed_text_or_ancestry_rejects_reused_objects(self):
        self.tracker.snapshot.return_value = (b"new page text", (3, ()))
        self.assertFalse(self.tracker.valid(self.target))
        self.tracker.snapshot.return_value = (b"original", (3, ()))
        self.tracker.parents.return_value = (self.control, accessible("/new-document"))
        self.assertFalse(self.tracker.valid(self.target))

    def test_closed_x_window_and_disconnected_accessibility_are_recoverable(self):
        for method in (
            self.tracker.connection.create_resource_object.return_value.get_attributes,
            self.tracker.snapshot,
        ):
            method.side_effect = RuntimeError("remote object gone")
            self.assertFalse(self.tracker.valid(self.target))
            method.side_effect = None

    def test_recycled_xid_with_different_pid_is_rejected(self):
        self.tracker.connection.create_resource_object.return_value.get_full_property.return_value = SimpleNamespace(
            value=[99]
        )
        self.assertFalse(self.tracker.valid(self.target))

    def test_reload_and_removed_document_invalidate_only_its_sessions(self):
        unrelated = Target(self.window, self.control, (accessible("/other-document"),))
        self.tracker.targets.add(unrelated)
        for kind in (
            "document:reload",
            "document:load-complete",
            "object:children-changed:remove",
        ):
            self.target.invalid = False
            event = SimpleNamespace(type=kind, source=self.document, any_data=self.document)
            self.tracker.event(event)
            self.assertTrue(self.target.invalid)
            self.assertFalse(unrelated.invalid)

    def test_missing_accessibility_and_non_editable_focus_still_return_recovery_target(self):
        self.tracker.find_focused = Mock(return_value=self.control)
        self.tracker.usable.return_value = False
        with patch("dictadesk.desktop.destination.destination", return_value=self.window):
            captured = self.tracker.capture()
        self.assertEqual(captured.window, self.window)
        self.assertIsNone(captured.control)
        self.assertFalse(self.tracker.valid(captured))

    def test_paste_confirmation_never_accepts_unexpected_field_changes(self):
        self.target.expected = b"expected insertion"
        self.tracker.snapshot.return_value = (b"unexpected edits", (3, ()))
        self.assertFalse(self.tracker.advance(self.target))
        self.assertTrue(self.target.invalid)
        self.assertEqual(self.target.content, b"original")

    def test_successful_phrase_advances_only_that_session_caret(self):
        self.target.expected = b"expected insertion"
        self.tracker.snapshot.return_value = (b"expected insertion", (15, ()))
        self.assertTrue(self.tracker.advance(self.target))
        self.assertEqual(self.target.position, (15, ()))
        self.assertEqual(self.target.content, b"expected insertion")

    def test_return_focus_does_not_steal_from_an_intervening_user_action(self):
        self.tracker.owns_focus = Mock(return_value=False)
        self.tracker.activate = Mock()
        self.tracker.return_focus(Target(Destination(200, 201, 99)), self.target)
        self.tracker.activate.assert_not_called()
