# SPDX-License-Identifier: GPL-3.0-only
# ruff: noqa: E402
"""Real GTK clipboard sequencing with synthetic phrases and isolated destinations."""

import os
import time
from unittest.mock import patch

assert os.environ["HOME"].startswith("/tmp/dictadesk-test.")
from dictadesk.ui.application import Application
from dictadesk.ui.gtk import Gdk, GLib
from dictadesk.desktop.delivery import Destination
from test_continuous import FakeCapture, FakeRecognizer, VOICE


class LiveCapture(FakeCapture):
    def run(self, started):
        started()
        for _ in range(3):
            self.phrases.put(VOICE * 2)
        limit = time.monotonic() + 8
        while (
            not self.stop.wait(0.02) and not self.cancel.is_set() and time.monotonic() < limit
        ):
            pass
        self.done.set()


class Keyboard:
    connection = object()
    down = set()
    modifiers = False
    escape = False

    def set_escape(self, enabled):
        self.escape = enabled

    def poll(self):
        pass

    def modifiers_down(self):
        return self.modifiers

    def close(self):
        pass


app = Application(isolated=True)
expected = Destination(100, 101, 200)
current = expected
sent = []
failures = []
move_on_first = False


def send(connection, target):
    global current
    clipboard = Gdk.Display.get_default().get_clipboard()

    def read(clip, result):
        sent.append(clip.read_text_finish(result))

    clipboard.read_text_async(None, read)
    if move_on_first:
        current = Destination(300, 301, 400)
    return True


def until(condition, next_step):
    deadline = time.monotonic() + 10

    def poll():
        try:
            assert time.monotonic() < deadline, "Timed out waiting for continuous operation"
            if not condition():
                return True
            next_step()
        except Exception as exc:
            import traceback

            traceback.print_exc()
            failures.append(exc)
            app.request_quit()
        return False

    GLib.timeout_add(50, poll)


def start():
    app.keyboard = Keyboard()
    c = app.session
    c.microphone_problem = ""
    c.recognizer = FakeRecognizer()
    c.preferences.spoken_punctuation = True
    c.preferences.continuous_live = True  # Super+V must still select phrase mode.
    app.key_pressed("record")
    assert c.pause_dictation
    assert c.capture.limit == c.preferences.max_seconds
    app.key_released("record")  # Releasing the ordinary shortcut must not stop this mode.
    assert c.continuous and not c.capture.stop.is_set()
    until(lambda: len(sent) == 3, stop)


def stop():
    assert sent == ["Hello, world", ".", " Next…"], sent
    assert app.keyboard.escape
    app.key_pressed("finish")
    assert not app.session.cancelled.is_set(), "Escape cancelled instead of finishing"
    until(lambda: not app.session.busy, finished)


def finished():
    assert not app.session.pause_dictation
    assert len(sent) == 3, "Finalization duplicated the document"
    sent.clear()
    app.session.recognizer = FakeRecognizer()
    app.key_pressed("record")
    until(lambda: len(sent) == 3, stop_with_super_v)


def stop_with_super_v():
    app.key_pressed("record")
    assert app.session.capture.stop.is_set()
    until(lambda: not app.session.busy, finished_twice)


def finished_twice():
    global move_on_first
    assert len(sent) == 3, "Finalization duplicated the document"
    assert not app.keyboard.escape
    assert not app.session.pending_text
    assert app.session.result.text == "Hello, world. Next…"
    sent.clear()
    move_on_first = True
    app.session.preferences.continuous_live = False
    app.session.recognizer = FakeRecognizer()
    app.key_pressed("continuous")
    until(lambda: not app.session.busy, moved)


def moved():
    global current, move_on_first
    assert sent == ["Hello, world"], sent
    assert app.session.pending_text == ". Next…", app.session.pending_text
    assert app.session.result.text == "Hello, world. Next…"
    current = expected
    move_on_first = False
    app.key_pressed("paste")
    until(lambda: not app.session.busy and len(sent) == 2, recovered)


def recovered():
    assert sent == ["Hello, world", ". Next…"], sent
    assert not app.session.pending_text
    sent.clear()
    app.session.recognizer = FakeRecognizer()
    app.keyboard.modifiers = True
    app.key_pressed("continuous")
    until(lambda: bool(app.session.result.text) and bool(app.session.pending_text), cancel)


def cancel():
    app.session.cancel()
    app.keyboard.modifiers = False
    until(lambda: not app.session.busy, cancelled)


def cancelled():
    assert sent == [], sent
    assert app.session.pending_text == "Hello, world"
    assert "already inserted" in app.session.notice
    print(
        "PASS continuous GTK: toggle, incremental clipboard, no final duplication, focus-loss stop, pending-only recovery, held modifiers and cancellation"
    )
    app.request_quit()


with (
    patch("dictadesk.core.session.check"),
    patch("dictadesk.core.session.microphones", return_value=[]),
    patch("dictadesk.audio.continuous.ContinuousCapture", LiveCapture),
    patch("dictadesk.ui.application.destination", side_effect=lambda _: current),
    patch("dictadesk.ui.application.paste", side_effect=send),
):
    until(lambda: app.session is not None, start)
    app.run(["dictadesk-continuous-test"])
assert not failures, failures
