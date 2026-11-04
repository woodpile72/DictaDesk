# SPDX-License-Identifier: GPL-3.0-only
# ruff: noqa: E402
"""Shortcut model warm-up decisions on an isolated GTK desktop; no recording."""

import os
from unittest.mock import patch

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
        c.microphone_problem = ""

        def loading():
            c.phase = "loading"
            app.refresh()

        def reset():
            c.phase, c.notice = "idle", ""
            c.recognizer.loaded = False
            c.cancelled.clear()
            app.pending_start = None
            app.target_lost = False

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

        for continuous_mode in (False, True):
            with (
                patch.object(c, "load_available_model", side_effect=loading),
                patch.object(c, "record_in_window") as record,
                patch.object(c, "record_continuous") as continuous,
            ):
                reset()
                app.refresh()
                assert app.lookup_action("record").get_enabled()
                app.record_from_button(continuous=continuous_mode)
                assert app.pending_start and c.phase == "loading"
                record.assert_not_called()
                continuous.assert_not_called()
                loaded()
                record.assert_called_once_with(continuous=continuous_mode)
                record.reset_mock()
                continuous.reset_mock()
                # A second press or Escape cancels the queued recording, not the load.
                for cancel in (app.record_from_button, app.escape_pressed):
                    reset()
                    app.record_from_button(continuous=continuous_mode)
                    cancel()
                    loaded()
                    record.assert_not_called()
                    continuous.assert_not_called()
                # Joining the startup load must not restart it.
                reset()
                c.phase = "loading"
                app.record_from_button(continuous=continuous_mode)
                assert app.pending_start
                loaded()
                record.assert_called_once_with(continuous=continuous_mode)
                record.reset_mock()
                continuous.reset_mock()
                reset()
                app.record_from_button(continuous=continuous_mode)
                c.phase, c.notice = "idle", "Model load failed."
                app.resume_pending_start()
                assert not app.pending_start
                record.assert_not_called()
                continuous.assert_not_called()

        reset()
        with (
            patch.object(c, "load_available_model") as missing,
            patch.object(app, "show_preferences") as preferences,
            patch.object(c, "record") as record,
        ):
            app.record_from_button()
            missing.assert_called_once()
            preferences.assert_called_once()
            record.assert_not_called()
            assert not app.pending_start
            assert "download" in c.notice
        for problem in ("microphone", "credentials"):
            reset()
            c.recognizer.loaded = True
            c.microphone_problem = "Microphone unavailable" if problem == "microphone" else ""
            c.preferences.backend = "openai" if problem == "credentials" else "local"
            c.credential_available = False
            with (
                patch.object(app, "show_preferences") as preferences,
                patch.object(c, "record") as record,
            ):
                app.record_from_button()
                preferences.assert_called_once()
                record.assert_not_called()
        c.preferences.backend = "local"
        c.microphone_problem = ""
        reset()
        c.pending_text = "Keep this text"
        with (
            patch.object(app, "show_transcript") as recover,
            patch.object(c, "load_available_model") as load,
        ):
            app.record_from_button()
            recover.assert_called_once()
            load.assert_not_called()
        c.pending_text = ""

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
    patch("dictadesk.ui.application.destination", side_effect=lambda _: current),
):
    GLib.timeout_add(100, exercise)
    app.run(["dictadesk-shortcut-loading-test"])
assert not failures, failures
