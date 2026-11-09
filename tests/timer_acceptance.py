# SPDX-License-Identifier: GPL-3.0-only
# ruff: noqa: E402
"""Private timer lifecycle/UI test; no actual microphone, playback or desktop notices."""

import os
from unittest.mock import Mock, patch

assert os.environ["HOME"].startswith("/tmp/dictadesk-test.")
from dictadesk.ui.application import Application
from dictadesk.ui.gtk import Adw, GLib
from dictadesk.desktop.tray import Tray

Adw.init()
app = Application(isolated=True)
errors = []


def run():
    try:
        app.show_preferences("commands")
        assert app.preferences_dialog.timer_status.is_ancestor(
            app.preferences_dialog.pages["commands"]
        )
        with (
            patch.object(app.speech, "preview") as speak,
            patch.object(app.speech, "ready", return_value=True),
        ):
            app.session.preferences.tts_enabled = False
            app.confirm_timer(1440)
            speak.assert_not_called()
            app.session.preferences.tts_enabled = True
            app.confirm_timer(1440)
            assert speak.call_args.args[0].tts_phrase == "A timer has been set for 24 minutes."
            assert speak.call_args.kwargs["confirmation"]
            speak.reset_mock()
            with patch.object(app.speech, "ready", return_value=False):
                app.confirm_timer(60)
                speak.assert_not_called()
            app.session.preferences.tts_enabled = False
        tray = object.__new__(Tray)
        tray.app = app
        invocation = Mock()
        tray.menu_call(
            None,
            None,
            None,
            None,
            "GetProperty",
            GLib.Variant("(is)", (14, "label")),
            invocation,
        )
        invocation.return_dbus_error.assert_called_once()
        clock = [100]
        app.countdown.clock = lambda: clock[0]
        with (
            patch.object(app.handsfree, "valid", return_value=True),
            patch.object(app.handsfree, "complete"),
        ):
            app.run_desktop_command("timer", ("set", 1440), 0)
        assert app.countdown.active and app.countdown.remaining() == 1440
        rows = Tray.rows(Mock(app=app))
        assert 13 not in rows and rows[14][0] == "Cancel timer"
        assert not any("Help" in row[0] for row in rows.values())
        app.notifications_enabled = True
        with (
            patch.object(app, "send_notification") as notify,
            patch.object(app.alarm, "start") as sound,
        ):
            with (
                patch.object(app.handsfree, "valid", return_value=True),
                patch.object(app.handsfree, "complete"),
            ):
                app.run_desktop_command("timer", ("status",), 0)
            notify.assert_called_once()
            notify.reset_mock()
            clock[0] += 1441
            # Isolate dictation state through its existing read-only busy property.
            with patch.object(
                type(app.session), "busy", new_callable=lambda: property(lambda self: True)
            ):
                app.speech.confirming = True
                with patch.object(app.speech, "stop_playback") as stop_confirmation:
                    app.tick_countdown()
                    stop_confirmation.assert_called_once()
                app.speech.confirming = False
                sound.assert_not_called()
            notify.assert_called_once()
            app.tick_countdown()
            sound.assert_called_once()
            app.tick_countdown()
            sound.assert_called_once()
            assert app.countdown.elapsed
            app.cancel_timer()
            assert not app.countdown.exists and not app.alarm_pending
            assert 14 not in Tray.rows(Mock(app=app))
        print(
            "PASS Timer: local voice routing, tray time left, preferences status, expiry once, sound defers during dictation, cancel/dismiss"
        )
    except Exception as exc:
        import traceback

        traceback.print_exc()
        errors.append(exc)
    app.request_quit()
    return False


with (
    patch("dictadesk.core.session.check"),
    patch("dictadesk.core.session.microphones", return_value=[]),
    patch("dictadesk.core.session.Session.load_available_model"),
    patch("dictadesk.core.handsfree.HandsFree.tick"),
    patch("dictadesk.ui.preferences.read_key", return_value=""),
    patch("dictadesk.ui.preferences.downloaded", return_value=True),
    patch(
        "dictadesk.ui.preferences.compute_capabilities",
        return_value={"cuda": False, "reason": "fixture"},
    ),
):
    GLib.timeout_add(400, run)
    app.run(["timer-test"])
assert not errors, errors
