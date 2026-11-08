# SPDX-License-Identifier: GPL-3.0-only
"""Session-bound X11/AT-SPI destinations. Missing evidence always means recovery.

No shell helpers, window titles, widget names or tree indices identify a field.
Keep the actual remote object and its ancestry, including the browser document.
"""

from dataclasses import dataclass
import hashlib
import time
import weakref

from Xlib import X, protocol

from .delivery import Destination, destination


@dataclass(eq=False)
class Target:
    window: Destination | None
    control: object = None
    ancestry: tuple = ()
    position: object = None
    content: object = None
    invalid: bool = False
    explicit: bool = False
    deferred: bool = False
    expected: object = None


def identity(obj):
    return (obj.app.bus_name, obj.path)


class DestinationTracker:
    def __init__(self, connection, *, accessibility=True):
        self.connection = connection
        self.focused = None
        self.targets = weakref.WeakSet()
        self.listener = None
        self.api = None
        if not accessibility:
            return
        try:
            import gi

            gi.require_version("Atspi", "2.0")
            from gi.repository import Atspi

            self.api = Atspi
            Atspi.set_timeout(150, 150)
            self.listener = Atspi.EventListener.new(self.event)
            for event in (
                "object:state-changed:focused",
                "object:children-changed:remove",
                "document:load-complete",
                "document:reload",
            ):
                self.listener.register(event)
        except (ImportError, ValueError, RuntimeError):
            # X11 shortcuts and deliberate clipboard recovery still work.
            self.api = None

    def event(self, event, *_):
        if event.type.startswith("object:state-changed:focused"):
            if event.detail1:
                self.focused = event.source
            return
        try:
            removed = event.any_data if "children-changed" in event.type else event.source
            key = identity(removed)
            for target in self.targets:
                if any(identity(obj) == key for obj in target.ancestry):
                    target.invalid = True
        except Exception:
            pass

    def states(self, obj):
        obj.clear_cache()
        return obj.get_state_set()

    def usable(self, obj):
        api = self.api
        state = self.states(obj)
        return (
            not state.contains(api.StateType.DEFUNCT)
            and state.contains(api.StateType.EDITABLE)
            and state.contains(api.StateType.ENABLED)
            and state.contains(api.StateType.SENSITIVE)
            and obj.get_role() != api.Role.PASSWORD_TEXT
            and obj.get_text_iface() is not None
            and obj.get_component_iface() is not None
        )

    def parents(self, obj):
        chain = []
        for _ in range(64):
            if obj is None:
                return tuple(chain)
            if identity(obj) in [identity(x) for x in chain]:
                raise ValueError("Cyclic accessibility ancestry")
            chain.append(obj)
            obj = obj.get_parent()
        raise ValueError("Accessibility ancestry is too deep to verify")

    def belongs(self, obj, window):
        """Match the accessible frame to the actual X client, not just its PID.

        Browser content can run in another process. The enclosing accessible
        application's PID and frame geometry must both match the X11 client.
        """
        api = self.api
        if not window or not window.pid:
            return False
        if obj.get_application().get_process_id() != window.pid:
            return False
        client = self.connection.create_resource_object("window", window.client)
        geometry = client.get_geometry()
        translated = self.connection.screen().root.translate_coords(client, 0, 0)
        for parent in self.parents(obj):
            if parent.get_role() in (api.Role.FRAME, api.Role.DIALOG, api.Role.WINDOW):
                if not self.states(parent).contains(api.StateType.ACTIVE):
                    return False
                rect = parent.get_component_iface().get_extents(api.CoordType.SCREEN)
                # Decorations differ across toolkits; require the X client centre
                # inside the accessible frame and a comparable frame size.
                cx, cy = translated.x + geometry.width / 2, translated.y + geometry.height / 2
                return (
                    rect.x <= cx < rect.x + rect.width
                    and rect.y <= cy < rect.y + rect.height
                    and abs(rect.width - geometry.width) < 100
                    and abs(rect.height - geometry.height) < 100
                )
        return False

    def find_focused(self, window):
        if not self.api:
            return None
        api = self.api
        try:
            if self.focused and self.states(self.focused).contains(api.StateType.FOCUSED):
                if self.belongs(self.focused, window):
                    return self.focused
        except Exception:
            self.focused = None
        desktop = api.get_desktop(0)
        deadline = time.monotonic() + 0.6
        for index in range(desktop.get_child_count()):
            if time.monotonic() > deadline:
                break
            app = desktop.get_child_at_index(index)
            if app.get_process_id() != window.pid:
                continue
            # Collection queries avoid crawling entire browser accessibility trees.
            collection = app.get_collection_iface()
            if collection:
                rule = api.MatchRule.new(
                    api.StateSet.new([api.StateType.FOCUSED]),
                    api.CollectionMatchType.ALL,
                    {},
                    api.CollectionMatchType.ALL,
                    [],
                    api.CollectionMatchType.ALL,
                    [],
                    api.CollectionMatchType.ALL,
                    False,
                )
                for obj in collection.get_matches(
                    rule, api.CollectionSortOrder.CANONICAL, 8, True
                ):
                    if self.belongs(obj, window):
                        self.focused = obj
                        return obj
            # Some toolkits omit Collection. Bounded walk is only a bootstrap;
            # subsequent sessions use native focus events.
            pending = [app]
            visited = 0
            while pending and visited < 256 and time.monotonic() < deadline:
                obj = pending.pop()
                visited += 1
                if self.states(obj).contains(api.StateType.FOCUSED) and self.belongs(
                    obj, window
                ):
                    self.focused = obj
                    return obj
                pending.extend(
                    obj.get_child_at_index(i) for i in range(min(obj.get_child_count(), 128))
                )
        return None

    def snapshot(self, obj):
        text = obj.get_text_iface()
        count = text.get_character_count()
        if count < 0 or count > 1_000_000:
            raise ValueError("Field is too large to verify safely")
        content = hashlib.sha256(self.api.Text.get_text(text, 0, count).encode()).digest()
        selections = tuple(
            (selection.start_offset, selection.end_offset)
            for selection in (
                self.api.Text.get_selection(text, i) for i in range(text.get_n_selections())
            )
        )
        return content, (text.get_caret_offset(), selections)

    def capture(self, *, explicit=False):
        try:
            window = destination(self.connection)
        except Exception:
            window = None
        target = Target(window, explicit=explicit)
        self.targets.add(target)
        if not window:
            return target
        try:
            obj = self.find_focused(window)
            if obj and self.usable(obj):
                content, position = self.snapshot(obj)
                target.ancestry = self.parents(obj)
                target.content, target.position = content, position
                target.control = obj
            # Ensure an accessibility round trip didn't capture a later window.
            if destination(self.connection) != window:
                target.invalid = True
        except Exception:
            pass
        return target

    def valid(self, target):
        if not target or target.invalid or not target.window:
            return False
        try:
            window = self.connection.create_resource_object("window", target.window.client)
            window.get_attributes()  # BadWindow is a recoverable stale destination.
            prop = window.get_full_property(
                self.connection.intern_atom("_NET_WM_PID"), X.AnyPropertyType
            )
            if not prop or int(prop.value[0]) != target.window.pid:
                return False
            if target.explicit and not target.control:
                # A deliberate Paste Last Transcript shortcut authorizes only
                # the exact currently focused X window, never refocusing it.
                return destination(self.connection) == target.window
            if not target.control or not self.usable(target.control):
                return False
            current = self.parents(target.control)
            if tuple(map(identity, current)) != tuple(map(identity, target.ancestry)):
                return False
            if any(
                self.states(obj).contains(self.api.StateType.DEFUNCT)
                or self.states(obj).contains(self.api.StateType.BUSY)
                for obj in current
            ):
                return False
            return self.snapshot(target.control)[0] == target.content
        except Exception:
            return False

    def focused_on(self, target):
        if not self.valid(target):
            return False
        return self.owns_focus(target)

    def owns_focus(self, target):
        current = destination(self.connection)
        if (
            not current
            or current.client != target.window.client
            or current.pid != target.window.pid
        ):
            return False
        return (
            self.states(target.control).contains(self.api.StateType.FOCUSED)
            if target.control
            else current == target.window
        )

    def activate(self, window):
        client = self.connection.create_resource_object("window", window.client)
        event = protocol.event.ClientMessage(
            window=client,
            client_type=self.connection.intern_atom("_NET_ACTIVE_WINDOW"),
            data=(32, [2, X.CurrentTime, 0, 0, 0]),
        )
        self.connection.screen().root.send_event(
            event, event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask
        )
        self.connection.flush()

    def restore(self, target):
        if not self.valid(target):
            return False
        if target.explicit and not target.control:
            return self.focused_on(target)
        current = destination(self.connection)
        if not current or current.client != target.window.client:
            self.activate(target.window)
        elif not self.states(target.control).contains(self.api.StateType.FOCUSED):
            target.control.get_component_iface().grab_focus()
        # Focus requests are asynchronous, particularly in browsers. The caller
        # verifies exact field focus on subsequent ticks before touching text.
        return True

    def restore_position(self, target):
        if not self.focused_on(target):
            return False
        if not target.control:
            return True
        text = target.control.get_text_iface()
        caret, selections = target.position
        for i in reversed(range(text.get_n_selections())):
            if not text.remove_selection(i):
                return False
        if not text.set_caret_offset(caret):
            return False
        for start, end in selections:
            if not text.add_selection(start, end):
                return False
        return self.snapshot(target.control)[1] == target.position

    def expect(self, target, value):
        if not target.control:
            return
        text = target.control.get_text_iface()
        original = self.api.Text.get_text(text, 0, text.get_character_count())
        caret, selections = target.position
        if len(selections) > 1:
            raise ValueError("Multiple selections cannot be safely pasted")
        start, end = selections[0] if selections else (caret, caret)
        expected = original[:start] + value + original[end:]
        target.expected = hashlib.sha256(expected.encode()).digest()

    def advance(self, target):
        """Acknowledge editable fields only after their text reflects the paste."""
        if not target.control:
            return target.explicit
        if target.invalid:
            return False
        content, position = self.snapshot(target.control)
        if content == target.content and content != target.expected:
            return None  # Clipboard request/paste has not completed yet.
        if content != target.expected:
            target.invalid = True
            return False
        target.content, target.position = content, position
        target.expected = None
        return True

    def return_focus(self, previous, target):
        # Restoring the user's application is best-effort and cannot turn a
        # confirmed insertion into a failure. Do not steal intervening focus.
        try:
            if previous and previous.window and self.owns_focus(target):
                window = self.connection.create_resource_object(
                    "window", previous.window.client
                )
                prop = window.get_full_property(
                    self.connection.intern_atom("_NET_WM_PID"), X.AnyPropertyType
                )
                if not prop or int(prop.value[0]) != previous.window.pid:
                    return
                if previous.window.client != target.window.client:
                    self.activate(previous.window)
                if previous.control and self.valid(previous):
                    previous.control.get_component_iface().grab_focus()
        except Exception:
            pass

    def close(self):
        if self.listener:
            for event in (
                "object:state-changed:focused",
                "object:children-changed:remove",
                "document:load-complete",
                "document:reload",
            ):
                try:
                    self.listener.deregister(event)
                except Exception:
                    pass
            self.listener = None
