# SPDX-License-Identifier: GPL-3.0-only
"""Bounded, disposable in-memory audio. This module never writes audio to disk."""

from array import array
import math
import queue
import threading
import time

from .input import RATE, resolve

BLOCK_SECONDS = 0.1
MAX_AUDIO_SECONDS = 8


def speech_level(data):
    values = array("h", data)
    return math.sqrt(sum(value * value for value in values) / max(1, len(values))) / 32768


class MemoryInput:
    def __init__(self, prefs):
        self.prefs = prefs
        self.blocks = queue.Queue(maxsize=5)
        self.error = ""
        self.stream = None
        self.closed = threading.Event()

    def __enter__(self):
        import sounddevice as sd

        def receive(data, frames, timing, status):
            if self.closed.is_set():
                return
            if status:
                self.error = (
                    "Microphone audio was interrupted. Toggle hands-free commands to retry."
                )
                return
            try:
                self.blocks.put_nowait(bytes(data))
            except queue.Full:
                # Recognition may take longer than capture. Never keep its backlog.
                self.discard()

        self.stream = sd.RawInputStream(
            device=resolve(self.prefs),
            samplerate=RATE,
            channels=1,
            dtype="int16",
            blocksize=1600,
            callback=receive,
        )
        try:
            self.stream.start()
        except BaseException:
            self.stream.close()
            raise
        return self

    def discard(self):
        while True:
            try:
                self.blocks.get_nowait()
            except queue.Empty:
                return

    def read(self, stop, deadline):
        while not stop.is_set() and time.monotonic() < deadline:
            if self.error:
                raise RuntimeError(self.error)
            try:
                return self.blocks.get(timeout=0.1)
            except queue.Empty:
                pass
        return None

    def __exit__(self, *_):
        self.closed.set()
        if self.stream:
            self.stream.abort()
            self.stream.close()
        self.discard()


def utterance(source, stop, *, threshold, deadline, require_quiet=False, max_seconds=8):
    """Consume one utterance, requiring fresh speech after any boundary reset."""
    audio = bytearray()
    voiced = quiet = 0
    armed = not require_quiet
    while not stop.is_set():
        block = source.read(stop, deadline)
        if block is None:
            break
        speaking = speech_level(block) >= threshold
        if not armed:
            quiet = 0 if speaking else quiet + 1
            if quiet >= 3:
                armed, quiet = True, 0
            continue
        if speaking:
            voiced += 1
            quiet = 0
        elif audio:
            quiet += 1
        if speaking or audio:
            audio.extend(block)
        if quiet >= 5 or len(audio) >= max_seconds * RATE * 2:
            return bytes(audio) if voiced >= 2 else b""
    return bytes(audio) if not stop.is_set() and voiced >= 2 else b""


def acknowledge(stop):
    """A short synthesized tone; caller has already closed microphone capture."""
    import sounddevice as sd

    rate = 16000
    samples = array(
        "h",
        (
            int(3500 * math.sin(2 * math.pi * 880 * i / rate) * math.sin(math.pi * i / 1920))
            for i in range(1920)
        ),
    )
    if stop.is_set():
        return
    with sd.RawOutputStream(samplerate=rate, channels=1, dtype="int16") as stream:
        stream.write(samples.tobytes())
