# SPDX-License-Identifier: GPL-3.0-only
"""Generated fixture speech only; never records a microphone or plays sound."""

import ctypes as c
import subprocess


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
