# SPDX-License-Identifier: GPL-3.0-only
"""Opt-in local inference test; downloads a model into .qa, never records audio."""

import argparse
import ctypes as c
from pathlib import Path
import subprocess
import threading
import wave
from dictadesk.audio.service import Recognizer

parser = argparse.ArgumentParser()
parser.add_argument("--download-model", action="store_true", required=True)
parser.parse_args()
root = Path(__file__).resolve().parents[1] / ".qa/real-speech"
root.mkdir(parents=True, exist_ok=True)
lib = c.CDLL("libespeak-ng.so.1")
lib.espeak_Initialize.argtypes = [c.c_int, c.c_int, c.c_char_p, c.c_int]
rate = lib.espeak_Initialize(2, 0, None, 0x8000)  # synchronous retrieval, no playback
assert rate > 0
blocks = []
callback_type = c.CFUNCTYPE(c.c_int, c.POINTER(c.c_short), c.c_int, c.c_void_p)


@callback_type
def collect(samples, count, events):
    if samples and count:
        blocks.append(c.string_at(samples, count * 2))
    return 0


lib.espeak_SetSynthCallback.argtypes = [callback_type]
lib.espeak_SetSynthCallback(collect)
lib.espeak_SetVoiceByName.argtypes = [c.c_char_p]
lib.espeak_SetVoiceByName(b"en-us")
lib.espeak_Synth.argtypes = [
    c.c_char_p,
    c.c_size_t,
    c.c_uint,
    c.c_int,
    c.c_uint,
    c.c_uint,
    c.c_void_p,
    c.c_void_p,
]
phrase = (
    b"This is a test of voice dictation. The microphone is ready. Please write a short message."
)
assert lib.espeak_Synth(phrase, len(phrase) + 1, 0, 1, 0, 1, None, None) == 0
with wave.open(str(root / "original.wav"), "wb") as out:
    out.setnchannels(1)
    out.setsampwidth(2)
    out.setframerate(rate)
    out.writeframes(b"".join(blocks))
subprocess.run(
    [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(root / "original.wav"),
        "-ar",
        "16000",
        str(root / "sample.wav"),
    ],
    check=True,
)
worker = Recognizer()
try:
    print("Loading the real Parakeet model in the independent test cache…", flush=True)
    worker.request(
        {"op": "load", "cache": str(root / "models"), "precision": "int8"},
        threading.Event(),
        timeout=900,
    )
    reply = worker.request(
        {"op": "transcribe", "path": str(root / "sample.wav")}, threading.Event()
    )
    print(reply["text"], flush=True)
    assert "test" in reply["text"].lower() and "message" in reply["text"].lower(), reply
    worker.close()
    worker.request(
        {"op": "load", "cache": str(root / "models"), "precision": "int8"},
        threading.Event(),
        timeout=180,
    )
    print("PASS real local speech and verified cached-model reload", flush=True)
finally:
    worker.close()
