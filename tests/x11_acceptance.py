# SPDX-License-Identifier: GPL-3.0-only
"""Real X11 protocol tests in a disposable Xvfb display, including stray-key prevention."""

import os
import time
from Xlib import X, XK, display
from Xlib.ext import xtest
from dictadesk.desktop.keyboard import Keyboard
from dictadesk.desktop.delivery import destination, paste

assert os.environ["HOME"].startswith("/tmp/dictadesk-test.")
client = display.Display()
root = client.screen().root
window = root.create_window(
    0,
    0,
    300,
    200,
    0,
    client.screen().root_depth,
    X.InputOutput,
    X.CopyFromParent,
    event_mask=X.KeyPressMask | X.KeyReleaseMask,
)
window.map()
window.set_input_focus(X.RevertToParent, X.CurrentTime)
client.sync()
events = []
keyboard = Keyboard(
    lambda name: events.append(("press", name)), lambda name: events.append(("release", name))
)
keyboard.configure(
    {
        "record": "<Super>v",
        "continuous": "<Super><Shift>v",
        "cancel": "<Super><Shift>x",
        "paste": "<Super><Shift>p",
    }
)
assert not keyboard.errors, keyboard.errors
competitor = Keyboard(lambda _: None, lambda _: None)
competitor.configure({"record": "<Super>v"})
assert "record" in competitor.errors, "Existing shortcut ownership was not detected"
competitor.close()

super_key = client.keysym_to_keycode(XK.string_to_keysym("Super_L"))
v = client.keysym_to_keycode(XK.string_to_keysym("v"))


def send(kind, key):
    xtest.fake_input(client, kind, key)
    client.sync()
    time.sleep(0.025)
    keyboard.poll()


for modifier_first in (False, True):
    events.clear()
    while client.pending_events():
        client.next_event()
    send(X.KeyPress, super_key)
    send(X.KeyPress, v)
    for _ in range(5):
        send(X.KeyPress, v)
    assert events == [("press", "record")], events
    send(X.KeyRelease, super_key if modifier_first else v)
    send(X.KeyRelease, v if modifier_first else super_key)
    assert events == [("press", "record"), ("release", "record")], events
    leaked = []
    while client.pending_events():
        event = client.next_event()
        if event.type == X.KeyPress and event.detail == v:
            leaked.append(event)
    assert not leaked, "The dictation key reached the destination window"

expected = destination(keyboard.connection)
events.clear()
shift = client.keysym_to_keycode(XK.string_to_keysym("Shift_L"))
send(X.KeyPress, super_key)
send(X.KeyPress, shift)
send(X.KeyPress, v)
send(X.KeyPress, v)
send(X.KeyRelease, v)
send(X.KeyRelease, shift)
send(X.KeyRelease, super_key)
assert events == [("press", "continuous"), ("release", "continuous")], events
escape = client.keysym_to_keycode(XK.string_to_keysym("Escape"))
while client.pending_events():
    client.next_event()
events.clear()
send(X.KeyPress, escape)
send(X.KeyRelease, escape)
assert not events, "Escape was intercepted outside continuous dictation"
assert any(client.next_event().type == X.KeyPress for _ in range(client.pending_events()))
keyboard.set_escape(True)
events.clear()
while client.pending_events():
    client.next_event()
send(X.KeyPress, escape)
keyboard.set_escape(False)  # Finishing quickly must still consume this key's repeats.
send(X.KeyPress, escape)
send(X.KeyRelease, escape)
assert events == [("press", "finish"), ("release", "finish")], events
assert "finish" not in keyboard.chords
leaked = []
while client.pending_events():
    event = client.next_event()
    if event.type == X.KeyPress and event.detail == escape:
        leaked.append(event)
assert not leaked, "Escape leaked into the destination while finishing dictation"
events.clear()
send(X.KeyPress, escape)
send(X.KeyRelease, escape)
assert not events, "Escape remained reserved after dictation"
assert paste(keyboard.connection, expected)
other = root.create_window(
    10,
    10,
    100,
    100,
    0,
    client.screen().root_depth,
    X.InputOutput,
    X.CopyFromParent,
    event_mask=X.KeyPressMask,
)
other.map()
other.set_input_focus(X.RevertToParent, X.CurrentTime)
client.sync()
assert not paste(keyboard.connection, expected), "Pasted into a different destination"
keyboard.scan()
events.clear()
send(X.KeyPress, super_key)
send(X.KeyPress, v)
send(X.KeyRelease, v)
send(X.KeyRelease, super_key)
assert events == [("press", "record"), ("release", "record")]
keyboard.clear()
keyboard.poll()
assert not keyboard.chords, "Shortcut capture re-enabled itself while Preferences was open"
keyboard.close()
other.destroy()
window.destroy()
client.close()
print(
    "PASS X11: repeat suppression, both release orders, new windows, focus-change paste refusal and suspension"
)
