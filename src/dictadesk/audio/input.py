# SPDX-License-Identifier: GPL-3.0-only
"""Bounded microphone capture. No device is opened by enumeration or readiness checks."""

import queue
import tempfile
import threading
import time
import wave
import math
from array import array
from pathlib import Path

from ..core.diagnostics import event, failure

RATE = 16000


class AudioLevels:
    """Aggregate measurements only; no audio is retained for diagnostics."""

    def __init__(self, threshold):
        self.threshold = threshold
        self.samples = self.voiced = self.clipped = 0
        self.squares = self.peak = self.max_rms = 0.0
        self.last_block = time.monotonic()
        self.next_report = self.last_block + 5

    def observe(self, data):
        values = array("h", data)
        if not values:
            raise ValueError("Empty microphone callback")
        squares = sum(v * v for v in values)
        rms = math.sqrt(squares / len(values)) / 32768
        peak = max(abs(v) for v in values) / 32768
        self.samples += len(values)
        self.squares += squares
        self.peak = max(self.peak, peak)
        self.max_rms = max(self.max_rms, rms)
        self.voiced += len(values) if rms >= self.threshold else 0
        self.clipped += sum(abs(v) >= 32760 for v in values)
        self.last_block = time.monotonic()
        return peak, rms, len(values)

    def report(self, name, **metadata):
        event(
            name,
            samples=self.samples,
            rate=RATE,
            peak=round(self.peak, 6),
            rms=round(math.sqrt(self.squares / max(1, self.samples)) / 32768, 6),
            max_block_rms=round(self.max_rms, 6),
            voiced_samples=self.voiced,
            clipped_samples=self.clipped,
            threshold=self.threshold,
            **metadata,
        )

    def check_callbacks(self):
        if time.monotonic() >= self.next_report:
            self.report("audio.progress")
            self.next_report = time.monotonic() + 5
        if time.monotonic() - self.last_block > 5:
            raise RuntimeError(
                "The microphone stopped supplying audio. Try recording again or choose another input in Preferences."
            )


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
        self.levels = AudioLevels(prefs.silence_threshold)

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
            try:
                block = bytes(data)
                self.level, _, _ = self.levels.observe(block)
                self.peak = max(self.peak, self.level)
                chunks.put_nowait(block)
            except queue.Full:
                self.error = "Audio could not be captured fast enough. Please try again."
                self.stop.set()
            except Exception as exc:
                failure("audio.callback_failed", exc)
                self.error = "Microphone audio could not be read. Try recording again."
                self.stop.set()

        try:
            with wave.open(str(path), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(RATE)
                self.levels.last_block = time.monotonic()
                with sd.RawInputStream(
                    device=resolve(self.prefs),
                    samplerate=RATE,
                    channels=1,
                    dtype="int16",
                    blocksize=1600,
                    callback=receive,
                ):
                    event("audio.started", mode="sample_or_single", limit=self.limit)
                    started()
                    deadline = time.monotonic() + self.limit
                    while (
                        not self.stop.is_set()
                        and not self.cancel.is_set()
                        and time.monotonic() < deadline
                    ):
                        self.levels.check_callbacks()
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
            self.levels.report(
                "audio.stopped",
                mode="sample_or_single",
                cancelled=self.cancel.is_set(),
                failed=bool(self.error),
            )
