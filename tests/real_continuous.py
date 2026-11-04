# SPDX-License-Identifier: GPL-3.0-only
"""Use generated speech from real_speech.py; never opens the microphone or a remote API."""

from pathlib import Path
from array import array
import queue
import tempfile
import threading
import time
from unittest.mock import patch
import wave
import argparse
from cffi import FFI

from dictadesk.audio.service import Recognizer
from dictadesk.core.models import variant_directory
from dictadesk.core.session import Session
from dictadesk.core.settings import SettingsStore

root = Path(__file__).resolve().parents[1] / ".qa/real-speech"
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--whisper", action="store_true")
parser.add_argument(
    "--model", choices=["parakeet", "whisper-base", "whisper-tiny", "moonshine-tiny"]
)
parser.add_argument("--live", action="store_true")
parser.add_argument("--window", action="store_true")
parser.add_argument("--delivery-only", action="store_true", help="Check delivery independently of recognition accuracy; report text for review.")
args = parser.parse_args()
model_id = args.model or ("whisper-base" if args.whisper else "parakeet")
cache = {
    "parakeet": root / "models",
    "whisper-base": root.parent / "whisper-models",
    "moonshine-tiny": root.parent / "model-options",
    "whisper-tiny": root.parent / "model-options",
}[model_id]
assert (variant_directory(cache, "int8", model_id) / "current.json").is_file(), (
    "Download the selected test model first"
)
with wave.open(str(root / "sample.wav")) as source:
    assert source.getframerate() == 16000
    # A pause with low background noise and one loud spike per callback.
    noisy_pause = array("h", [10000] + [120, -120] * 799 + [120]).tobytes()
    pcm = source.readframes(source.getnframes()) + noisy_pause * 10
worker = Recognizer()
worker.request(
    {
        "op": "load",
        "cache": str(cache),
        "precision": "int8",
        "local_model": model_id,
        "local_only": True,
    },
    threading.Event(),
)
callbacks = queue.Queue()
sent = []
copied = []
session = None
in_window = args.window
live = args.live or in_window
speech_finished = threading.Event()
delivered_live = threading.Event()
feed_errors = []


class GeneratedInput:
    def __init__(self, **kwargs):
        self.callback = kwargs["callback"]

    def __enter__(self):
        def feed():
            ffi = FFI()
            for phrase in range(2):
                for offset in range(0, len(pcm), 3200):
                    if offset >= len(pcm) - len(noisy_pause) * 10:
                        speech_finished.set()
                    block = pcm[offset : offset + 3200]
                    storage = ffi.new("char[]", block)
                    buffer = ffi.buffer(storage, len(block))
                    self.callback(buffer, len(block) // 2, None, None)
                    buffer[:] = bytes(len(block))
                    time.sleep(0.1 if live else 0.002)
                if phrase == 0:
                    # Keep the stream OPEN until transcription arrives. Previously
                    # stopping first let a final flush masquerade as live delivery.
                    if not delivered_live.wait(20):
                        feed_errors.append(
                            "No text delivered while microphone stream remained open"
                        )
                        break
            session.finish_recording()

        self.thread = threading.Thread(target=feed)
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.thread.join()


try:
    with (
        tempfile.TemporaryDirectory() as scratch,
        patch.dict("os.environ", {"XDG_CACHE_HOME": scratch}),
        patch("sounddevice.RawInputStream", GeneratedInput),
    ):

        def changed():
            if (
                in_window
                and session.in_window
                and session.inline_text
                and not delivered_live.is_set()
            ):
                assert not copied, "Copied before recording finished"
                assert not speech_finished.is_set(), "Draft waited for a pause"
                assert not session.capture.stop.is_set(), "Draft waited for Stop"
                delivered_live.set()

        session = Session(
            callbacks.put,
            changed,
            (lambda text, target: copied.append((text, target)))
            if in_window
            else (
                lambda *_: (_ for _ in ()).throw(
                    AssertionError("Unexpected whole-document paste")
                )
            ),
            SettingsStore(Path(scratch) / "settings"),
            worker,
        )
        session.microphone_problem = ""
        session.preferences.local_model = model_id
        session.preferences.continuous_live = live

        def deliver(text, target, done):
            if not sent:
                if live:
                    assert not speech_finished.is_set(), "First insertion waited for a pause"
                assert not session.capture.stop.is_set(), "First insertion waited for Stop"
                assert not session.capture.done.is_set(), (
                    "First insertion waited for stream close"
                )
                delivered_live.set()
            sent.append(text)
            done(True)

        session.stream_delivered = deliver
        if in_window:
            session.record_in_window()
        else:
            session.record_continuous(target=object())
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                callbacks.get(timeout=0.05)()
            except queue.Empty:
                pass
            if not session.thread.is_alive() and callbacks.empty():
                break
        assert not session.thread.is_alive()
        assert not feed_errors, feed_errors
        assert delivered_live.is_set(), "No insertion during recording"
        if in_window:
            assert not sent
            assert copied == [(session.result.text, None)]
            assert session.inline_text == session.result.text
        else:
            assert "".join(sent) == session.result.text
        if not args.delivery_only:
            assert session.result.text.lower().count("microphone") == 2, session.result.text
            assert session.result.text.lower().count("message") == 2, session.result.text
        else:
            assert session.result.text.strip()
        assert not session.pending_text
        if not in_window:
            assert len(sent) >= 2
        assert not list(Path(scratch).rglob("*.wav"))
        print(
            f"PASS real dictation: window={in_window}, live={live}, text BEFORE Stop, no duplicates, copy/paste behavior verified, temporary audio removed"
        )
        print(session.result.text)
        if args.delivery_only:
            print("Recognition accuracy was not an acceptance condition for this delivery-only run.")
finally:
    if session and session.capture:
        session.capture.cancel.set()
    worker.close()
