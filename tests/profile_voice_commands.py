# SPDX-License-Identifier: GPL-3.0-only
"""Opt-in real local model / synthetic audio profiling; never opens a microphone."""

import argparse
import base64
import ctypes as c
import json
import os
from pathlib import Path
import subprocess
import threading
import time
from types import SimpleNamespace

import psutil

from dictadesk.audio.service import Recognizer
from dictadesk.core.handsfree import HandsFree
from dictadesk.core.settings import Preferences
from dictadesk.core.voice_commands import wake_matches, command, choice_number

parser = argparse.ArgumentParser()
parser.add_argument("--model-cache", type=Path, required=True)
parser.add_argument("--seconds", type=float, default=30)
args = parser.parse_args()
os.environ["HF_HUB_OFFLINE"] = "1"
worker = Recognizer()


def synthesize(text):
    lib = c.CDLL("libespeak-ng.so.1")
    lib.espeak_Initialize.argtypes = [c.c_int, c.c_int, c.c_char_p, c.c_int]
    rate = lib.espeak_Initialize(2, 0, None, 0x8000)
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
    data = text.encode()
    assert lib.espeak_Synth(data, len(data) + 1, 0, 1, 0, 1, None, None) == 0
    return subprocess.check_output(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-f",
            "s16le",
            "-ar",
            str(rate),
            "-ac",
            "1",
            "-i",
            "pipe:0",
            "-f",
            "s16le",
            "-ar",
            "16000",
            "-ac",
            "1",
            "pipe:1",
        ],
        input=b"".join(blocks),
    )


class Silence:
    def __init__(self, prefs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def discard(self):
        pass

    def read(self, stop, deadline):
        return None if stop.wait(0.1) else bytes(3200)


try:
    start = time.monotonic()
    worker.request(
        {
            "op": "load",
            "local_only": True,
            "cache": str(args.model_cache),
            "precision": "int8",
            "local_model": "parakeet",
            "device": "cpu",
        },
        threading.Event(),
        timeout=180,
    )
    result = {
        "model": "Parakeet Int8 / CPU",
        "model_load_seconds": time.monotonic() - start,
        "input": "Synthetic English speech and real-time synthetic silence; no microphone or playback",
    }
    trials = []
    samples = {}
    for text in [
        "Clanker.",
        "Hello computer.",
        "Open Firefox.",
        "Launch Calibre.",
        "Cancel.",
        "One.",
        "Two.",
        "Three.",
        "Four.",
        "Five.",
        "More.",
    ]:
        data = synthesize(text)
        samples[text] = data
        start = time.monotonic()
        reply = worker.request(
            {"op": "transcribe_pcm", "audio": base64.b64encode(data).decode()},
            threading.Event(),
        )
        trials.append(
            {
                "synthetic_phrase": text,
                "recognized": reply["text"],
                "inference_seconds": time.monotonic() - start,
                "audio_seconds": len(data) / 32000,
            }
        )
    result["spoken_choices"] = [
        {
            "spoken": trial["synthetic_phrase"],
            "recognized": trial["recognized"],
            "choice": choice_number(trial["recognized"]),
        }
        for trial in trials[-6:]
    ]
    result["default_wake_matched"] = wake_matches(trials[0]["recognized"], "Clanker")
    result["alternate_wake_matched"] = wake_matches(trials[1]["recognized"], "Hello computer")
    result["firefox_command_matched"] = command(trials[2]["recognized"]) == (
        "open",
        ("firefox",),
    )
    result["calibre_command_matched"] = command(trials[3]["recognized"]) == (
        "open",
        ("calibre",),
    )
    result["speech_trials"] = trials
    session = SimpleNamespace(
        preferences=Preferences(hands_free=True),
        busy=False,
        closing=False,
        dispatch=lambda fn: fn(),
        recognizer=worker,
    )
    hf = HandsFree(session, lambda: None, lambda *_: None, lambda *_: None, source=Silence)
    thread = threading.Thread(target=hf.run, args=(0, hf.stop, session.preferences))
    thread.start()
    processes = [psutil.Process(), psutil.Process(worker.process.pid)]

    def cpu():
        return sum(p.cpu_times().user + p.cpu_times().system for p in processes)

    def rss():
        return sum(p.memory_info().rss for p in processes) / 1024**2

    time.sleep(1)
    before_cpu, before_rss, peak, start = cpu(), rss(), rss(), time.monotonic()
    while time.monotonic() - start < args.seconds:
        peak = max(peak, rss())
        time.sleep(0.2)
    elapsed = time.monotonic() - start
    result["idle"] = {
        "seconds": elapsed,
        "combined_cpu_percent_one_core": (cpu() - before_cpu) / elapsed * 100,
        "combined_rss_start_mib": before_rss,
        "combined_rss_end_mib": rss(),
        "combined_peak_rss_mib": peak,
        "recognition_calls_during_silence": hf.recognitions,
    }
    stopped = time.monotonic()
    hf.suspend()
    thread.join(2)
    assert not thread.is_alive()
    assert hf.recognitions == 0
    result["capture_stop_seconds"] = time.monotonic() - stopped
    utterances = [samples["Hello computer."], samples["Open Firefox."]]
    opened = []
    active = [0]

    class Speech:
        def __init__(self, prefs):
            data = bytes(3200) * 4 + utterances.pop(0) + bytes(3200) * 6
            self.blocks = iter(
                [data[i : i + 3200].ljust(3200, b"\0") for i in range(0, len(data), 3200)]
            )

        def __enter__(self):
            active[0] += 1
            return self

        def __exit__(self, *_):
            active[0] -= 1

        def discard(self):
            pass

        def read(self, stop, deadline):
            if stop.wait(0.02):
                return None
            return next(self.blocks, None)

    def launch(query, generation, *, heard):
        opened.append(query)
        engine.stop.set()

    def beep(stop):
        assert active[0] == 0, "Microphone overlapped acknowledgment"

    session.preferences.wake_phrase = "Hello computer"
    engine = HandsFree(session, lambda: None, launch, lambda *_: None, source=Speech, beep=beep)
    pipeline = threading.Thread(target=engine.run, args=(0, engine.stop, session.preferences))
    pipeline.start()
    pipeline.join(15)
    engine.suspend()
    pipeline.join(2)
    assert opened == [("firefox",)], opened
    result["synthetic_end_to_end"] = {
        "wake_phrase": "Hello computer",
        "command": "open Firefox",
        "launch_callback": opened,
        "recognition_calls": engine.recognitions,
    }

    print(json.dumps(result, indent=2))
finally:
    worker.close()
