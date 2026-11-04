# SPDX-License-Identifier: GPL-3.0-only
"""Bounded microphone capture. No device is opened by enumeration or readiness checks."""

import queue
import tempfile
import threading
import time
import wave
from array import array
from pathlib import Path

RATE = 16000


def microphones():
    import sounddevice as sd

    hosts = sd.query_hostapis()
    return [
        (i, item["name"], hosts[item["hostapi"]]["name"])
        for i, item in enumerate(sd.query_devices())
        if item["max_input_channels"] > 0
    ]


def resolve(prefs):
    if not prefs.microphone:
        return None
    matches = [
        i
        for i, name, host in microphones()
        if (name, host) == (prefs.microphone, prefs.host_api)
    ]
    if len(matches) != 1:
        raise RuntimeError(
            "The selected microphone is missing or ambiguous. Choose an input in Preferences."
        )
    return matches[0]


def check(prefs):
    import sounddevice as sd

    device = resolve(prefs)
    sd.query_devices(device, "input")
    sd.check_input_settings(device=device, channels=1, dtype="int16", samplerate=RATE)


class Capture:
    def __init__(self, prefs, directory, seconds=None):
        self.prefs, self.directory = prefs, Path(directory)
        self.limit = seconds or prefs.max_seconds
        self.stop = threading.Event()
        self.cancel = threading.Event()
        self.done = threading.Event()
        self.level = 0.0
        self.peak = 0.0
        self.error = ""
        self.samples = 0
        self.previewer = None

    def run(self, started):
        import sounddevice as sd

        if self.cancel.is_set():
            self.done.set()
            raise InterruptedError()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        audio = tempfile.NamedTemporaryFile(
            suffix=".wav", prefix="recording-", dir=self.directory, delete=False
        )
        path = Path(audio.name)
        audio.close()
        chunks = queue.Queue(maxsize=100)

        def receive(data, frames, timing, status):
            if status:
                self.error = (
                    "Audio was interrupted. Try a different microphone or input settings."
                )
                self.stop.set()
            block = bytes(data)
            samples = array("h", block)
            self.level = max((abs(value) for value in samples), default=0) / 32768
            self.peak = max(self.peak, self.level)
            try:
                chunks.put_nowait(block)
            except queue.Full:
                self.error = "Audio could not be captured fast enough. Please try again."
                self.stop.set()

        try:
            with wave.open(str(path), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(RATE)
                with sd.RawInputStream(
                    device=resolve(self.prefs),
                    samplerate=RATE,
                    channels=1,
                    dtype="int16",
                    blocksize=1600,
                    callback=receive,
                ):
                    started()
                    deadline = time.monotonic() + self.limit
                    while (
                        not self.stop.is_set()
                        and not self.cancel.is_set()
                        and time.monotonic() < deadline
                    ):
                        try:
                            data = chunks.get(timeout=0.1)
                            output.writeframesraw(data)
                            if self.previewer:
                                self.previewer.add(data)
                            self.samples += len(data) // 2
                        except queue.Empty:
                            pass
                while not chunks.empty() and not self.cancel.is_set():
                    data = chunks.get_nowait()
                    output.writeframesraw(data)
                    self.samples += len(data) // 2
            if self.cancel.is_set():
                raise InterruptedError("Recording cancelled")
            if self.error:
                raise RuntimeError(self.error)
            if self.samples < RATE // 5 or self.peak < 0.001:
                raise RuntimeError(
                    "No speech-level audio was captured. Check your microphone in Preferences."
                )
            return path
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        finally:
            self.done.set()
