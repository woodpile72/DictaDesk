# SPDX-License-Identifier: GPL-3.0-only
# ruff: noqa: E402
"""Shortcut model warm-up decisions on an isolated GTK desktop; no recording."""

import os
from unittest.mock import patch, Mock

assert os.environ["HOME"].startswith("/tmp/dictadesk-test.")
from dictadesk.ui.application import Application
from dictadesk.ui.gtk import GLib
from dictadesk.desktop.delivery import Destination

app = Application(isolated=True)
expected = Destination(100, 101, 200)
current = expected
failures = []


class Keyboard:
    connection = object()
    down = set()
    errors = {}

    def poll(self):
        pass

    def set_escape(self, enabled):
        self.escape = enabled

    def close(self):
        pass


def exercise():
    global current
    if not app.session:
        return True
    try:
        c = app.session
        app.keyboard = Keyboard()
        app.destinations = Mock()
        app.destinations.capture.side_effect = lambda **_: expected
        app.destinations.focused_on.side_effect = lambda target: target == current
        c.microphone_problem = ""

        def loading():
            c.phase = "loading"
            app.refresh()

        def reset():
            c.phase, c.notice = "idle", ""
            c.recognizer.loaded = False
            c.cancelled.clear()
            app.pending_start = None

        def loaded():
            c.phase = "idle"
            c.recognizer.loaded = True
            app.resume_pending_start()

        with (
            patch.object(c, "load_available_model", side_effect=loading) as load,
            patch.object(c, "record") as record,
            patch.object(c, "record_continuous") as continuous,
        ):
            reset()
            app.key_pressed("continuous")
            assert app.keyboard.escape
            assert c.phase == "loading"
            continuous.assert_not_called()
            loaded()
            continuous.assert_called_once_with(target=expected)
            continuous.reset_mock()

            reset()
            app.key_pressed("record")
            record.assert_not_called()
            continuous.assert_not_called()
            loaded()
            continuous.assert_called_once_with(target=expected, pause_mode=True)
            continuous.reset_mock()

            reset()
            app.key_pressed("record")
            assert app.keyboard.escape
            app.key_released("record")
            assert app.pending_start
            loaded()
            continuous.assert_called_once_with(target=expected, pause_mode=True)
            continuous.reset_mock()
            reset()
            app.key_pressed("record")
            app.key_pressed("record")
            loaded()
            continuous.assert_not_called()
            record.assert_not_called()

            for finish in (
                lambda: app.key_pressed("continuous"),
                lambda: app.key_pressed("finish"),
                app.escape_pressed,
            ):
                reset()
                app.key_pressed("continuous")
                finish()
                assert not app.pending_start
                assert not c.cancelled.is_set(), (
                    "Stopping a pending start discarded model loading"
                )
                loaded()
                continuous.assert_not_called()

            reset()
            app.key_pressed("continuous")
            current = Destination(300, 301, 400)
            loaded()
            continuous.assert_not_called()
            assert "Destination changed" in c.notice
            current = expected

            reset()
            app.key_pressed("continuous")
            c.phase, c.notice = "idle", "Fixture model load failure"
            app.resume_pending_start()
            assert not app.pending_start and "failure" in c.notice
            continuous.assert_not_called()
            load.assert_called()

        reset()
        with (
            patch.object(c, "load_available_model") as missing,
            patch.object(c, "record_continuous") as continuous,
            patch.object(app, "notify_issue") as notify,
        ):
            app.key_pressed("record")
            missing.assert_called_once()
            assert not app.pending_start
            continuous.assert_not_called()
            notify.assert_called_once_with(
                "Download a speech model", "Choose a local model in Preferences."
            )

        print(
            "PASS shortcut warm-up: both tap shortcuts, release during loading, Escape/second-press, focus loss, failed/missing model"
        )
    except Exception as exc:
        import traceback

        traceback.print_exc()
        failures.append(exc)
    app.request_quit()
    return False


with (
    patch("dictadesk.core.session.check"),
    patch("dictadesk.core.session.microphones", return_value=[]),
):
    GLib.timeout_add(100, exercise)
    app.run(["dictadesk-shortcut-loading-test"])
assert not failures, failures
