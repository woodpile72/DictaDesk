# SPDX-License-Identifier: GPL-3.0-only
# ruff: noqa: E402
"""Repeated keyboard dictation after delivery/recognition failures on a private desktop."""

import os
import time
from unittest.mock import patch

assert os.environ["HOME"].startswith("/tmp/dictadesk-test.")
from dictadesk.ui.application import Application
from dictadesk.ui.gtk import Gdk, GLib
from destination_mock import Tracker
from test_continuous import FakeCapture


class Keyboard:
    connection = object()
    down = set()
    errors = {}

    def poll(self):
        pass

    def set_escape(self, enabled):
        pass

    def modifiers_down(self):
        return False

    def close(self):
        pass


class Recognizer:
    loaded = True
    answer = "First fresh recording."

    def request(self, *_args, **_kwargs):
        if isinstance(self.answer, Exception):
            raise self.answer
        return {"text": self.answer}

    def close(self):
        self.loaded = False


app = Application(isolated=True)
tracker = Tracker()
tracker.current.invalid = True  # Origin cannot be safely restored.
errors = []
step = 0
retained = ""
notifications = []


def checked(fn):
    try:
        fn()
    except Exception as error:
        import traceback

        traceback.print_exc()
        errors.append(error)
        app.request_quit()


def until_idle(fn):
    deadline = time.monotonic() + 10

    def poll():
        if not app.session.busy:
            checked(fn)
            return False
        if time.monotonic() > deadline:
            checked(lambda: (_ for _ in ()).throw(AssertionError("Recording stuck")))
            return False
        return True

    GLib.timeout_add(30, poll)


def read_clipboard(fn):
    def read(clipboard, result):
        checked(lambda: fn(clipboard.read_text_finish(result)))

    Gdk.Display.get_default().get_clipboard().read_text_async(None, read)


def begin():
    c = app.session
    app.keyboard = Keyboard()
    app.destinations = tracker
    c.recognizer = Recognizer()
    c.microphone_problem = ""
    app.notify_recording = lambda kind, body="": notifications.append(kind)
    c.recording_event = app.notify_recording
    assert app.lookup_action("transcript") is None
    assert app.get_windows() == []
    Gdk.Display.get_default().get_clipboard().set("Original clipboard")
    record()


def record():
    app.key_pressed("continuous" if step == 1 else "record")
    if step == 4:
        app.key_pressed("cancel")
    until_idle(lambda: read_clipboard(finished))


def finished(value):
    global step, retained
    c = app.session
    assert c.can_record
    assert not hasattr(c, "pending_text")
    assert app.get_windows() == [], "Failure opened a recovery window"
    if step in (0, 1, 5):
        assert value == c.result.text and value.strip(), (value, c.notice)
        assert "clipboard" in c.notice
        assert "attention" in notifications
        retained = value
        assert c.last_dictation == retained
    else:
        assert value == retained, "Empty, failed or cancelled recording replaced clipboard"
        assert c.last_dictation == retained
    step += 1
    if step == 6:
        app.deliver("", tracker.current)
        app.deliver(None, tracker.current)
        assert c.can_record
        print(
            "PASS recovery: repeated standard/extended shortcuts after failed insertion, clipboard fallback, no recovery UI/state, empty/error/cancel recovery, last completed dictation retained"
        )
        app.request_quit()
        return
    c.recognizer.answer = {
        1: "Second fresh recording.",
        2: "",
        3: RuntimeError("Fixture transcription failed"),
        4: "Cancelled result",
        5: "Fresh recording after failures.",
    }[step]
    notifications.clear()
    record()


with (
    patch("dictadesk.core.session.check"),
    patch("dictadesk.core.session.microphones", return_value=[]),
    patch("dictadesk.audio.continuous.ContinuousCapture", FakeCapture),
    patch("dictadesk.audio.continuous.IncrementalCapture", FakeCapture),
):
    GLib.timeout_add(200, lambda: (checked(begin), False)[1])
    app.run(["dictadesk-recovery-test"])
assert not errors, errors
