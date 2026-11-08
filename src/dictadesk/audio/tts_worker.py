# SPDX-License-Identifier: GPL-3.0-only
"""Isolated optional synthesis. JSON in/out; no microphone, playback or downloads."""

import base64
import contextlib
import ctypes as c
import json
import os
from pathlib import Path
import sys

from ..core.diagnostics import event, failure
from ..core.tts import SpeechStore, VOICES


class EspeakVoice:
    def __init__(self, voice):
        self.library = lib = c.CDLL("libespeak-ng.so.1")
        lib.espeak_Initialize.argtypes = [c.c_int, c.c_int, c.c_char_p, c.c_int]
        lib.espeak_Initialize.restype = c.c_int
        self.rate = lib.espeak_Initialize(2, 0, None, 0x8000)
        if self.rate <= 0:
            raise ValueError(
                "System eSpeak NG could not initialize. Check its voice-data package."
            )
        lib.espeak_SetVoiceByName.argtypes = [c.c_char_p]
        # eSpeak's British voice is named "en" even though its locale is en-GB.
        if lib.espeak_SetVoiceByName(("en" if voice == "en-gb" else voice).encode()) != 0:
            raise ValueError("The selected eSpeak NG voice is unavailable.")
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
        callback = c.CFUNCTYPE(c.c_int, c.POINTER(c.c_short), c.c_int, c.c_void_p)
        self.blocks = []
        self.samples = 0

        @callback
        def collect(samples, count, events):
            self.samples += max(0, count)
            if self.samples > self.rate * 30:
                return 1
            if samples and count:
                self.blocks.append(c.string_at(samples, count * 2))
            return 0

        self.callback = collect
        lib.espeak_SetSynthCallback.argtypes = [callback]
        lib.espeak_SetSynthCallback(collect)

    def synthesize(self, text):
        self.blocks, self.samples = [], 0
        data = text.encode("utf-8")
        result = self.library.espeak_Synth(data, len(data) + 1, 0, 1, 0, 1, None, None)
        if result or self.samples > self.rate * 30:
            raise ValueError("The spoken acknowledgment could not be synthesized.")
        return self.rate, b"".join(self.blocks)


def load(store, engine, model):
    store.verify(engine, model)
    if engine == "espeak":
        return EspeakVoice(model)
    sys.path.insert(0, str(store.runtime()))
    from piper import PiperVoice

    return PiperVoice.load(store.voice_folder(model) / (model + ".onnx"), use_cuda=False)


def synthesize(voice, engine, text):
    if (
        not isinstance(text, str)
        or not text.strip()
        or len(text) > 120
        or not text.isprintable()
    ):
        raise ValueError("Use a short, printable spoken acknowledgment.")
    if engine == "espeak":
        return voice.synthesize(text)
    blocks, size, rate = [], 0, 0
    for chunk in voice.synthesize(text):
        if chunk.sample_width != 2 or chunk.sample_channels != 1:
            raise ValueError("The speech engine returned an unsupported audio format.")
        if rate and rate != chunk.sample_rate:
            raise ValueError("The speech engine changed its audio sample rate.")
        rate = chunk.sample_rate
        block = chunk.audio_int16_bytes
        size += len(block)
        if size > rate * 2 * 30:
            raise ValueError("The spoken acknowledgment is too long.")
        blocks.append(block)
    return rate, b"".join(blocks)


def main():
    voice = engine = store = None
    sys.dont_write_bytecode = True
    for line in sys.stdin:
        try:
            request = json.loads(line)
            with contextlib.redirect_stdout(sys.stderr):
                if request["op"] == "load":
                    engine, model = request["engine"], request["model"]
                    if model not in VOICES or VOICES[model].engine != engine:
                        raise ValueError("Choose a supported speech engine and voice.")
                    store = SpeechStore(Path(request["root"]))
                    voice = load(store, engine, model)
                    store.note_use()
                    result = {"loaded": True}
                elif request["op"] == "synthesize" and voice is not None:
                    rate, data = synthesize(voice, engine, request["text"])
                    if not data or not 8000 <= rate <= 48000:
                        raise ValueError("The speech engine produced no usable audio.")
                    result = {"rate": rate, "audio": base64.b64encode(data).decode("ascii")}
                    event("worker.speech_generated", samples=len(data) // 2, rate=rate)
                else:
                    raise ValueError("Load a speech voice before synthesis.")
        except Exception as exc:
            failure("worker.speech_failed", exc)
            result = {
                "error": str(exc)[:300]
                if isinstance(exc, ValueError)
                else "Speech synthesis failed. Check the selected engine and voice."
            }
        print(json.dumps(result), flush=True)
        request = None


if __name__ == "__main__":
    os.umask(0o077)
    main()
