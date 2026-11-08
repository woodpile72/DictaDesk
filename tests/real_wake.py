# SPDX-License-Identifier: GPL-3.0-only
"""Opt-in local ASR regression: generated wake audio, real segmentation and decoding.

No microphone, audio playback, network request or application launch is used.
"""

import argparse
from array import array
import json
from pathlib import Path
import threading
from types import SimpleNamespace

from dictadesk.audio.service import Recognizer
from dictadesk.core.handsfree import HandsFree
from dictadesk.core.settings import Preferences
from synthetic_speech import synthesize

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--model-cache", type=Path, required=True)
args = parser.parse_args()
worker = Recognizer()
trials = []


def run(wake, speech, offset_ms, *, threshold=0.015, gain=1, expected=True, inline=False):
    clips = []
    for text in (speech, "Open Firefox.") if expected and not inline else (speech,):
        pcm = synthesize(text)
        if gain != 1:
            pcm = array("h", (int(value * gain) for value in array("h", pcm))).tobytes()
        clips.append(bytes(16000 + offset_ms * 32) + pcm + bytes(16000))
    opened, beeps, active = [], [], []
    prefs = Preferences(hands_free=True, wake_phrase=wake, silence_threshold=threshold)
    session = SimpleNamespace(
        preferences=prefs,
        busy=False,
        closing=False,
        desktop_blocked=False,
        dispatch=lambda fn: fn(),
        recognizer=worker,
    )

    class Source:
        def __init__(self, _prefs):
            if not clips:
                engine.stop.set()
                data = b""
            else:
                data = clips.pop(0)
            self.blocks = iter(data[i : i + 3200] for i in range(0, len(data), 3200))

        def __enter__(self):
            active.append(self)
            return self

        def __exit__(self, *_):
            active.remove(self)

        def read(self, stop, deadline):
            return None if stop.is_set() else next(self.blocks, None)

    def beep(stop):
        assert not active, "Acknowledgment overlapped microphone capture"
        beeps.append(True)

    def launch(query, generation, *, heard):
        assert engine.activation_target is target
        opened.append(query)
        engine.stop.set()

    target = object()
    engine = HandsFree(
        session,
        lambda: None,
        launch,
        lambda *_args, **_kwargs: None,
        source=Source,
        beep=beep,
        capture_destination=lambda: target,
    )
    engine.run(0, engine.stop, prefs)
    matched = opened == [("firefox",)] and len(beeps) == (0 if inline else 1)
    trials.append(
        {
            "wake": wake,
            "spoken": speech,
            "offset_ms": offset_ms,
            "gain": gain,
            "threshold": threshold,
            "expected": expected,
            "inline": inline,
            "matched": matched,
            "beeps": len(beeps),
            "actions": len(opened),
        }
    )
    assert not active
    assert matched == expected, trials[-1]
    if not expected:
        assert not beeps and not opened, trials[-1]


try:
    worker.request(
        {
            "op": "load",
            "cache": str(args.model_cache),
            "precision": "int8",
            "local_model": "parakeet",
            "local_only": True,
        },
        threading.Event(),
    )
    for wake in ("Clanker", "Hey Clanker", "Hello computer"):
        for offset in (0, 50, 100, 150):
            run(wake, wake + ".", offset)
    run("Clanker", "Clanker", 50)
    run("Clanker", "Clanker.", 50, gain=0.5)
    run("Clanker", "Clanker.", 50, gain=0.1, threshold=0.003)
    run("Clanker", "Clanker open Firefox.", 50, inline=True)
    run("Hello computer", "Hello computer open Firefox.", 150, inline=True)
    for text in ("Thank you.", "Open Firefox.", "What is the weather today?"):
        run("Clanker", text, 50, expected=False)
    print(json.dumps({"generated_audio_only": True, "trials": trials}, indent=2))
    print(
        "PASS real local wake lifecycle: default/custom phrases, callback alignment, one acknowledgment/action, negative speech rejected, no microphone/playback/launch"
    )
finally:
    worker.close()
