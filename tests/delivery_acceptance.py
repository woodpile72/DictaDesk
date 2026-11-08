# SPDX-License-Identifier: GPL-3.0-only
# ruff: noqa: E402
"""Clipboard/paste state transitions on a private desktop; no real destination."""

import os
from unittest.mock import patch

assert os.environ["HOME"].startswith("/tmp/dictadesk-test.")
from dictadesk.ui.application import Application
from dictadesk.ui.gtk import GLib
from dictadesk.desktop.delivery import Destination
from dictadesk.desktop.destination import Target
from destination_mock import Tracker

app = Application(isolated=True)
errors = []
tracker = Tracker()
expected = tracker.current


class Keyboard:
    connection = object()
    down = set()
    modifiers = False

    def set_escape(self, enabled):
        pass

    def poll(self):
        return True

    def modifiers_down(self):
        return self.modifiers

    def close(self):
        pass


def later(fn):
    def call():
        try:
            fn()
        except Exception as exc:
            import traceback

            traceback.print_exc()
            errors.append(exc)
            app.request_quit()
        return False

    GLib.timeout_add(500, call)


def begin():
    c = app.session
    app.keyboard = Keyboard()
    app.destinations = tracker
    c.recognizer.loaded = True
    c.microphone_problem = ""
    c.result.replace("Keep this transcript.", [])
    app.deliver(c.result.text, expected)
    c.cancel()
    later(cancelled)


def cancelled():
    assert not app.session.busy
    send.assert_not_called()
    assert app.session.result.text == "Keep this transcript."
    app.key_pressed("paste")
    later(retry)


def retry():
    send.assert_called_once()
    assert not app.session.busy
    send.reset_mock()
    app.key_pressed("paste")
    tracker.current = Target(Destination(300, 301, 400))
    expected.invalid = True
    later(moved)


def moved():
    send.assert_not_called()
    assert not app.session.busy
    assert "failed" in app.session.notice
    assert app.session.pending_text == "Keep this transcript."
    tracker.current = expected
    expected.invalid = False
    app.keyboard.modifiers = True
    app.key_pressed("paste")
    later(held)


def held():
    assert app.session.busy
    send.assert_not_called()
    app.session.cancel()
    app.keyboard.modifiers = False
    later(held_cancelled)


def held_cancelled():
    assert not app.session.busy
    send.assert_not_called()
    send.side_effect = RuntimeError("disconnected")
    app.key_pressed("paste")
    later(failed)


def failed():
    assert not app.session.busy
    assert "failed" in app.session.notice
    assert app.session.result.text == "Keep this transcript."
    print(
        "PASS delivery: cancel, re-paste after cancellation, focus change, held modifiers, paste failure and retained transcript"
    )
    app.request_quit()


with (
    patch("dictadesk.core.session.check"),
    patch("dictadesk.core.session.microphones", return_value=[]),
    patch("dictadesk.ui.application.destination", side_effect=lambda _: tracker.current.window),
    patch("dictadesk.ui.application.paste", return_value=True) as send,
):
    later(begin)
    app.run(["dictadesk-delivery-test"])
assert not errors, errors
