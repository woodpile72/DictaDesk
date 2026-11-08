# SPDX-License-Identifier: GPL-3.0-only
# ruff: noqa: E402
"""Real X11 focus, AT-SPI fields, selection and clipboard on a private desktop."""

import os
from pathlib import Path
import subprocess
import time
from unittest.mock import patch

assert os.environ["HOME"].startswith("/tmp/dictadesk-test.")
from Xlib import X, Xatom, display
from dictadesk.desktop.destination import DestinationTracker
from dictadesk.ui.application import Application
from dictadesk.ui.gtk import GLib, Gdk

app = Application(isolated=True)
failures = []
child = None
connection = display.Display()
# A tiny private WM fixture implements only EWMH activation. No real WM needed.
wm = display.Display()
wm.screen().root.change_attributes(event_mask=X.SubstructureNotifyMask)
wm.flush()


class Keyboard:
    down = set()
    errors = {}

    def __init__(self):
        self.connection = connection

    def set_escape(self, enabled):
        pass

    def modifiers_down(self):
        return False

    def poll(self):
        while wm.pending_events():
            event = wm.next_event()
            if event.type == X.ClientMessage and event.client_type == wm.intern_atom(
                "_NET_ACTIVE_WINDOW"
            ):
                focus(event.window.id)

    def close(self):
        pass


def focus(ident):
    connection.set_input_focus(ident, X.RevertToParent, X.CurrentTime)
    connection.screen().root.change_property(
        connection.intern_atom("_NET_ACTIVE_WINDOW"), Xatom.WINDOW, 32, [ident]
    )
    connection.sync()


def later(callback, delay=350):
    def run():
        try:
            callback()
        except Exception as exc:
            import traceback

            traceback.print_exc()
            failures.append(exc)
            app.request_quit()
        return False

    GLib.timeout_add(delay, run)


def until(predicate, callback):
    deadline = time.monotonic() + 8

    def poll():
        assert time.monotonic() < deadline, "Destination operation timed out"
        if predicate():
            callback()
        else:
            later(poll, 50)

    later(poll, 50)


def text(obj):
    api = app.destinations.api
    return api.Text.get_text(obj, 0, -1)


def prepare():
    global child, original, second, third, origin_xid, other_xid
    app.keyboard = Keyboard()
    app.destinations = DestinationTracker(connection)
    child = subprocess.Popen(
        ["/usr/bin/python3", str(Path(__file__).with_name("destination_fixture.py"))],
        stdin=subprocess.PIPE,
        text=True,
        env={**os.environ, "GTK_A11Y": "always", "NO_AT_BRIDGE": "0"},
    )
    later(discover, 1200)


def discover():
    global original, second, third, origin_xid, other_xid
    windows = {w.get_wm_name(): w.id for w in connection.screen().root.query_tree().children}
    origin_xid = windows["DictaDesk origin fixture"]
    other_xid = windows["DictaDesk other fixture"]
    api = app.destinations.api
    found = {}

    def visit(obj):
        if obj.get_name() in ("Original field", "Other field", "Other window field"):
            found[obj.get_name()] = obj
        for i in range(obj.get_child_count()):
            visit(obj.get_child_at_index(i))

    visit(api.get_desktop(0))
    original, second, third = (
        found[n] for n in ("Original field", "Other field", "Other window field")
    )
    focus(origin_xid)
    original.get_component_iface().grab_focus()
    later(normal)


def deliver(value, target, callback):
    c = app.session
    c.pending_text = ""
    c.phase = "idle"
    c._begin("transcribing")
    c.result.replace(value, [])
    app.deliver(value, target)
    until(lambda: not c.busy, callback)


def normal():
    original.set_caret_offset(7)
    target = app.destinations.capture()
    assert target.control == original and app.destinations.valid(target)
    deliver("NORMAL", target, normal_done)


def normal_done():
    assert text(original) == "before NORMALselected after", text(original)
    assert not app.session.pending_text
    original.add_selection(7, 13)
    global target
    target = app.destinations.capture()
    second.get_component_iface().grab_focus()
    later(same_window)


def same_window():
    assert not app.destinations.focused_on(target)
    deliver("REPLACED", target, same_window_done)


def same_window_done():
    assert text(original) == "before REPLACEDselected after", text(original)
    assert text(second) == "Unrelated field"
    # Return to the precise field the user was working in after insertion.
    assert app.destinations.states(second).contains(app.destinations.api.StateType.FOCUSED)
    focus(origin_xid)
    original.get_component_iface().grab_focus()
    later(cross_window)


def cross_window():
    global target
    original.set_caret_offset(len(text(original)))
    target = app.destinations.capture()
    focus(other_xid)
    third.get_component_iface().grab_focus()
    later(lambda: deliver(" CROSS", target, cross_window_done))


def cross_window_done():
    assert text(original).endswith(" CROSS"), text(original)
    assert text(third) == "Other application text"
    assert connection.get_input_focus().focus.id == other_xid
    # Each new session must capture a fresh application/field.
    later(next_session)


def next_session():
    target = app.destinations.capture()
    assert target.control == third, (
        target,
        app.destinations.focused,
        third.get_state_set().get_states(),
    )
    deliver("NEW SESSION", target, repeated_done)


def repeated_done():
    assert "NEW SESSION" in text(third)
    focus(origin_xid)
    original.get_component_iface().grab_focus()
    later(changed_content)


def changed_content():
    target = app.destinations.capture()
    # Navigation/replacement can reuse an object: content must still match.
    original.get_editable_text_iface().set_text_contents("Page changed")
    deliver("RECOVER ME", target, recovery)


def recovery():
    assert text(original) == "Page changed"
    assert app.session.pending_text == "RECOVER ME"
    assert "failed" in app.session.notice
    app.copy_pending()
    assert not app.session.pending_text

    def read(clipboard, result):
        assert clipboard.read_text_finish(result) == "RECOVER ME"
        later(no_editable)

    Gdk.Display.get_default().get_clipboard().read_text_async(None, read)


def no_editable():
    child.stdin.write("button\n")
    child.stdin.flush()
    later(no_editable_ready)


def no_editable_ready():
    captured = app.destinations.capture()
    assert captured.control is None
    deliver("NO EDITABLE", captured, no_editable_done)


def no_editable_done():
    assert app.session.pending_text == "NO EDITABLE"
    assert text(second) == "Unrelated field"
    app.copy_pending()
    original.get_component_iface().grab_focus()
    later(removed)


def removed():
    global target
    target = app.destinations.capture()
    child.stdin.write("remove\n")
    child.stdin.flush()
    later(lambda: deliver("REMOVED", target, removed_done))


def removed_done():
    assert app.session.pending_text == "REMOVED"
    assert not app.destinations.valid(target)
    app.copy_pending()
    second.get_component_iface().grab_focus()
    later(closed)


def closed():
    global target
    target = app.destinations.capture()
    child.stdin.write("close\n")
    child.stdin.flush()
    focus(other_xid)
    third.get_component_iface().grab_focus()
    later(lambda: deliver("CLOSED", target, closed_done))


def closed_done():
    assert app.session.pending_text == "CLOSED"
    assert "CLOSED" not in text(third)
    assert app.session.result.text == "CLOSED"
    print(
        "PASS native X11/AT-SPI: normal paste, original selection, same-window field, cross-window focus restoration, return focus, repeated sessions, changed content, removed field, closed window, clipboard recovery"
    )
    app.request_quit()


with (
    patch("dictadesk.core.session.check"),
    patch("dictadesk.core.session.microphones", return_value=[]),
):
    later(prepare)
    try:
        app.run(["dictadesk-destination-test"])
    finally:
        if child:
            child.terminate()
            child.wait(timeout=5)
        connection.close()
        wm.close()
assert not failures, failures
