# SPDX-License-Identifier: GPL-3.0-only
# ruff: noqa: E402
"""Real GTK dispatch/worker handshake, with synthetic ASR, capture and playback."""

import os
import threading
import time
from unittest.mock import Mock, patch

assert os.environ["HOME"].startswith("/tmp/dictadesk-test.")
from dictadesk.ui.application import Application
from dictadesk.ui.gtk import GLib

app = Application(isolated=True)
errors, sequence = [], []
active = [0]
started = time.monotonic()


class Source:
    def __init__(self, prefs):
        pass

    def __enter__(self):
        active[0] += 1
        return self

    def __exit__(self, *args):
        active[0] -= 1


def start():
    c = app.session
    c.preferences.hands_free = True
    c.preferences.tts_enabled = True
    c.microphone_problem = ""
    c.recognizer = Mock(loaded=True)
    c.recognizer.request.side_effect = [
        {"text": "Computer volum up"},
        {"text": "background television"},
        {"text": ""},
        {"text": "yes"},
    ]
    h = app.handsfree
    h.source = Source
    h.capture_destination = lambda: None
    h.stop.clear()

    def sound(name):
        assert not active[0], "Microphone overlapped output"
        sequence.append(name)

    h.beep = lambda stop: sound("ready")
    h.accept_beep = lambda stop: sound("accepted")

    def speak(prefs, stop):
        assert "volume up" in prefs.tts_phrase
        sound("spoken choice")
        return True

    h.speech_acknowledge = speak

    def desktop(kind, arguments, valid, completed, **kwargs):
        assert valid() and not active[0]
        assert (kind, arguments) == ("volume", ("up", 5))
        sequence.append("executed")
        completed("")

    app.desktop_commands.execute = desktop
    h.thread = threading.Thread(
        target=h.run, args=(h.generation, h.stop, c.preferences), daemon=True
    )
    h.thread.start()
    return False


def check():
    try:
        if app.handsfree and app.handsfree.thread and not app.handsfree.thread.is_alive():
            assert sequence == ["spoken choice", "ready", "accepted", "executed"], sequence
            assert app.command_chooser is None
            print(
                "PASS integrated fuzzy flow: GTK chooser, local yes answer, spoken prompt, ready/accepted cues, action once; no overlapping capture/output"
            )
            app.request_quit()
            return False
        assert time.monotonic() - started < 12, sequence
        return True
    except Exception as exc:
        errors.append(exc)
        app.request_quit()
        return False


with (
    patch("dictadesk.core.session.Session.scan_devices"),
    patch("dictadesk.core.session.Session.load_available_model"),
    patch("dictadesk.core.handsfree.HandsFree.tick"),
    patch("dictadesk.audio.speech.SpeechOutput.tick"),
    patch("dictadesk.core.handsfree.utterance", return_value=bytes(3200)),
):
    GLib.timeout_add(300, start)
    GLib.timeout_add(100, check)
    app.run(["command-flow-test"])
assert not errors, errors
