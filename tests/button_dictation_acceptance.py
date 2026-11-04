# SPDX-License-Identifier: GPL-3.0-only
# ruff: noqa: E402
"""Button capture with a real GTK clipboard and synthetic audio/recognition."""

import os
import queue
import time
from unittest.mock import patch

assert os.environ["HOME"].startswith("/tmp/dictadesk-test.")
from dictadesk.audio.continuous import IncrementalCapture, AudioUpdate
from dictadesk.ui.application import Application
from dictadesk.ui.gtk import Gdk, GLib
from notification_host import NotificationHost
from test_continuous import VOICE

app = Application(isolated=True)
errors = []
app.notifications_enabled = True
host = NotificationHost()
notifications = host.messages


class Capture(IncrementalCapture):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.updates = queue.Queue()

    def has_updates(self):
        return not self.updates.empty()

    def next_update(self, timeout=0.1):
        return self.updates.get(timeout=timeout)

    def run(self, started):
        started()
        self.updates.put(AudioUpdate(0, VOICE * 2, False))
        deadline = time.monotonic() + 10
        while not self.stop.wait(0.02) and not self.cancel.is_set():
            if time.monotonic() > deadline:
                self.problem = "Fixture timed out waiting for Stop"
                break
        if not self.cancel.is_set():
            self.updates.put(AudioUpdate(0, VOICE * 2, True))
        self.done.set()


class Recognizer:
    loaded = True

    def __init__(self):
        self.responses = iter(["We will meat here", "We will meet here."])

    def request(self, *args, **kwargs):
        return {"text": next(self.responses)}

    def close(self):
        self.loaded = False


def checked(fn):
    try:
        fn()
    except Exception as exc:
        import traceback

        traceback.print_exc()
        errors.append(exc)
        app.request_quit()


def until(condition, fn):
    deadline = time.monotonic() + 10

    def poll():
        if time.monotonic() > deadline:
            errors.append(AssertionError("Timed out"))
            app.request_quit()
            return False
        if not condition():
            return True
        checked(fn)
        return False

    GLib.timeout_add(30, poll)


def read_clipboard(fn):
    def read(clipboard, result):
        checked(lambda: fn(clipboard.read_text_finish(result)))

    Gdk.Display.get_default().get_clipboard().read_text_async(None, read)


def start():
    c = app.session
    c.recognizer = Recognizer()
    c.microphone_problem = ""
    assert not app.window.inline_panel.get_visible()
    assert not hasattr(app.window, "cancel")
    Gdk.Display.get_default().get_clipboard().set("Existing clipboard")
    app.refresh()
    assert not notifications, notifications
    app.activate_action("record", None)
    until(lambda: bool(c.inline_text) and bool(notifications), lambda: read_clipboard(during))


def during(value):
    assert value == "Existing clipboard", value
    assert [n[3] for n in notifications] == ["Recording started"], notifications
    assert app.session.phase == "listening"
    assert app.window.inline_panel.get_visible()
    assert app.window.record.get_label() == "Stop Recording"
    buf = app.window.inline_result.get_buffer()
    assert buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False) == "We will meat here"
    app.activate_action("record", None)
    until(
        lambda: not app.session.busy and len(notifications) >= 3,
        lambda: read_clipboard(finished),
    )


def finished(value):
    assert value == "We will meet here.", value
    assert app.session.inline_text == value
    assert not app.session.pending_text
    assert app.window.inline_panel.get_visible()
    assert app.window.inline_status.get_text() == "Copied to clipboard"
    assert app.window.record.get_label() == "Start Recording"
    assert [n[3] for n in notifications] == [
        "Recording started",
        "Recording stopped",
        "Dictation finished",
    ], notifications
    assert notifications[0][1] == 0
    assert all(n[1] == 42 for n in notifications[1:]), notifications
    assert notifications[-1][4] == "Your text has been copied to the clipboard."
    assert all("We will" not in n[4] for n in notifications)
    print(
        "PASS desktop notifications: ordered start/stop/completion, replacement, no transcript disclosure"
    )
    print(
        "PASS button dictation: hidden until clicked, live revisable draft, Stop, final clipboard, no external paste"
    )
    app.request_quit()


with (
    patch("dictadesk.audio.continuous.IncrementalCapture", Capture),
    patch("dictadesk.core.session.check"),
    patch("dictadesk.core.session.microphones", return_value=[]),
):
    GLib.timeout_add(200, lambda: (checked(start), False)[1])
    app.run(["dictadesk-button-test"])
assert not errors, errors
