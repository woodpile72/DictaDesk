# SPDX-License-Identifier: GPL-3.0-only
"""X11 passive grabs, with physical release tracking and bounded event polling."""

import time
from dataclasses import dataclass

from Xlib import XK, X, display, error

MODIFIERS = {
    "Super": (X.Mod4Mask, ("Super_L", "Super_R")),
    "Control": (X.ControlMask, ("Control_L", "Control_R")),
    "Alt": (X.Mod1Mask, ("Alt_L", "Alt_R")),
    "Shift": (X.ShiftMask, ("Shift_L", "Shift_R")),
}


@dataclass
class Chord:
    key: str
    modifiers: tuple

    @classmethod
    def parse(cls, value):
        import re

        groups = re.findall(r"<([^>]+)>", value)
        key = re.sub(r"<[^>]+>", "", value)
        if (
            not groups
            or not any(m in groups for m in ("Super", "Control", "Alt"))
            or len(groups) != len(set(groups))
            or any(m not in MODIFIERS for m in groups)
            or len(key) != 1
            or not key.isascii()
            or not key.isalnum()
        ):
            raise ValueError(
                "Use a modifier and letter or digit, such as <Super>v or <Control><Alt>m."
            )
        return cls(key.lower(), tuple(m for m in MODIFIERS if m in groups))

    def __str__(self):
        return "+".join(self.modifiers + (self.key.upper(),)).replace("Control", "Ctrl")

    def codes(self, connection):
        return connection.keysym_to_keycode(XK.string_to_keysym(self.key))

    @property
    def mask(self):
        return sum(MODIFIERS[name][0] for name in self.modifiers)


class Keyboard:
    def __init__(self, pressed, released):
        self.connection = display.Display()
        self.root = self.connection.screen().root
        self.pressed, self.released = pressed, released
        self.chords, self.windows, self.down = {}, {}, set()
        self.errors = {}
        self.next_scan = 0
        self.escape_requested = False
        self.root.change_attributes(event_mask=X.SubstructureNotifyMask)

    def _windows(self):
        windows = {self.root.id: self.root}
        atom = self.connection.intern_atom("_NET_CLIENT_LIST")
        clients = self.root.get_full_property(atom, X.AnyPropertyType)
        ids = (
            list(clients.value) if clients else [w.id for w in self.root.query_tree().children]
        )
        for ident in ids:
            windows[int(ident)] = self.connection.create_resource_object("window", int(ident))
        focus = self.connection.get_input_focus().focus
        if hasattr(focus, "id"):
            windows[focus.id] = focus
        return windows

    def configure(self, mapping):
        chords = {name: Chord.parse(text) for name, text in mapping.items()}
        if len({(c.key, c.mask) for c in chords.values()}) != len(chords):
            raise ValueError(
                "Recording, continuous dictation, cancel and paste shortcuts must be different."
            )
        self.clear()
        self.chords = chords
        self.errors = {}
        self.scan()

    def scan(self):
        current = self._windows()
        for ident, window in current.items():
            if ident in self.windows:
                continue
            self.windows[ident] = window
            for name, chord in list(self.chords.items()):
                if name in self.errors:
                    continue
                failed = []
                for locks in (0, X.LockMask, X.Mod2Mask, X.LockMask | X.Mod2Mask):
                    window.grab_key(
                        chord.codes(self.connection),
                        chord.mask | locks,
                        False,
                        X.GrabModeAsync,
                        X.GrabModeAsync,
                        onerror=lambda exc, request, errors=failed: errors.append(exc) or True,
                    )
                self.connection.sync()
                if any(isinstance(exc, error.BadAccess) for exc in failed):
                    self.errors[name] = f"{chord} is already used by another application."
                    for owner in self.windows.values():
                        for locks in (0, X.LockMask, X.Mod2Mask, X.LockMask | X.Mod2Mask):
                            owner.ungrab_key(
                                chord.codes(self.connection),
                                chord.mask | locks,
                                onerror=lambda *_: True,
                            )
        for ident in set(self.windows) - set(current):
            window = self.windows.pop(ident)
            window.ungrab_key(X.AnyKey, X.AnyModifier, onerror=lambda *_: True)
        self.connection.flush()

    def set_escape(self, enabled):
        """Reserve Escape only for the lifetime of a continuous session."""
        self.escape_requested = enabled
        if enabled:
            if "finish" in self.chords:
                return
            self.chords["finish"] = Chord("Escape", ())
            # Revisit known windows without disturbing other active grabs.
            known, self.windows = self.windows, {}
            self.scan()
            self.windows.update(known)
        elif "finish" in self.chords:
            chord = self.chords["finish"]
            if self.held(chord):
                return  # Consume repeats until this Escape press is released.
            for window in self.windows.values():
                for locks in (0, X.LockMask, X.Mod2Mask, X.LockMask | X.Mod2Mask):
                    window.ungrab_key(
                        chord.codes(self.connection), locks, onerror=lambda *_: True
                    )
            self.chords.pop("finish", None)
            self.down.discard("finish")
            self.errors.pop("finish", None)
            self.connection.flush()

    def held(self, chord, bitmap=None):
        bitmap = bitmap or self.connection.query_keymap()

        def down(code):
            return bool(code and bitmap[code // 8] & (1 << (code % 8)))

        return down(chord.codes(self.connection)) and all(
            any(
                down(self.connection.keysym_to_keycode(XK.string_to_keysym(key)))
                for key in MODIFIERS[modifier][1]
            )
            for modifier in chord.modifiers
        )

    def modifiers_down(self):
        bitmap = self.connection.query_keymap()
        for _, (_, names) in MODIFIERS.items():
            for key in names:
                code = self.connection.keysym_to_keycode(XK.string_to_keysym(key))
                if code and bitmap[code // 8] & (1 << (code % 8)):
                    return True
        return False

    def poll(self):
        if time.monotonic() >= self.next_scan:
            self.scan()
            self.next_scan = time.monotonic() + 0.15
        while self.connection.pending_events():
            event = self.connection.next_event()
            if event.type != X.KeyPress:
                continue
            for name, chord in list(self.chords.items()):
                if (
                    name not in self.errors
                    and name not in self.down
                    and event.detail == chord.codes(self.connection)
                    and event.state & (X.ControlMask | X.ShiftMask | X.Mod1Mask | X.Mod4Mask)
                    == chord.mask
                ):
                    self.down.add(name)
                    self.pressed(name)
        bitmap = self.connection.query_keymap()
        for name in list(self.down):
            if name not in self.chords or not self.held(self.chords[name], bitmap):
                self.down.remove(name)
                self.released(name)
        if not self.escape_requested:
            self.set_escape(False)
        return True

    def clear(self):
        for window in self.windows.values():
            window.ungrab_key(X.AnyKey, X.AnyModifier, onerror=lambda *_: True)
        self.connection.sync()
        self.windows.clear()
        self.down.clear()
        self.chords.clear()
        self.escape_requested = False

    def close(self):
        self.clear()
        self.connection.close()
