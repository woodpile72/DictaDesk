# SPDX-License-Identifier: GPL-3.0-only
"""Deterministic destination adapter for UI lifecycle/worker tests."""

from dictadesk.desktop.destination import Target
from dictadesk.desktop.delivery import Destination


class Tracker:
    def __init__(self):
        self.current = Target(Destination(100, 101, 200), explicit=True)

    def capture(self, **_):
        return self.current

    def focused_on(self, target):
        return self.current is target and self.valid(target)

    def valid(self, target):
        return target is not None and not target.invalid

    def restore(self, target):
        if not self.valid(target):
            return False
        self.current = target
        return True

    def restore_position(self, target):
        return self.focused_on(target)

    def expect(self, target, value):
        pass

    def advance(self, target):
        return self.valid(target)

    def return_focus(self, previous, target):
        if self.current is target:
            self.current = previous

    def close(self):
        pass
