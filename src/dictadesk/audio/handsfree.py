# SPDX-License-Identifier: GPL-3.0-only
"""Bounded, disposable in-memory audio. This module never writes audio to disk."""

from array import array
from collections import deque
import math
import queue
import threading
import time

from .input import RATE, AudioLevels, resolve
from ..core.diagnostics import event, failure

BLOCK_SECONDS = 0.1
MAX_AUDIO_SECONDS = 8
PRE_ROLL_SAMPLES = RATE // 5


def speech_level(data, *, remove_dc=False):
    values = array("h", data)
    count = max(1, len(values))
    energy = sum(value * value for value in values) / count
    if remove_dc:
        energy -= (sum(values) / count) ** 2
    return math.sqrt(max(0, energy)) / 32768


class AdaptiveGate:
    """Estimate ambient RMS; the configured threshold remains the minimum.

    Learn only outside an utterance so speech cannot raise its own end boundary.
    Hysteresis allows speech to end at the ambient floor without needing digital
    silence. State contains levels only, never audio or recognized words.
    """

    def __init__(self, threshold):
        self.minimum = threshold
        self.reset()

    def reset(self):
        self.history = deque(maxlen=30)
        self.floor = 0.0
        self.start = self.end = self.minimum
        self.ready = self.active = False

    def observe(self, level, *, recording):
        if not recording:
            self.history.append(level)
            if len(self.history) < 3:
                return False
            self.floor = sorted(self.history)[len(self.history) // 5]
            self.start = max(self.minimum, self.floor * 1.6 + 0.003)
            self.end = max(self.minimum * 0.8, self.floor * 1.25 + 0.002)
            if not self.ready:
                event(
                    "handsfree.gate_ready",
                    noise_floor=round(self.floor, 6),
                    effective_threshold=round(self.start, 6),
                    minimum=self.minimum,
                    energy_mode="ac_rms",
                )
            self.ready = True
        if self.ready:
            self.active = level >= (self.end if self.active else self.start)
        return self.active


class MemoryInput:
    def __init__(self, prefs):
        self.prefs = prefs
        self.blocks = queue.Queue(maxsize=5)
        self.error = ""
        self.stream = None
        self.closed = threading.Event()
        self.levels = AudioLevels(prefs.silence_threshold)
        self.dropped = 0

    def __enter__(self):
        import sounddevice as sd

        def receive(data, frames, timing, status):
            if self.closed.is_set():
                return
            if status:
                self.error = "Microphone audio was interrupted. Listening will retry."
                return
            try:
                block = bytes(data)
                self.levels.observe(block)
                self.blocks.put_nowait(block)
            except queue.Full:
                # Recognition may take longer than capture. Never keep its backlog.
                self.discard()
                self.dropped += 1
            except Exception as exc:
                failure("audio.callback_failed", exc)
                self.error = "Microphone audio could not be read. Listening will retry."

        self.stream = sd.RawInputStream(
            device=resolve(self.prefs),
            samplerate=RATE,
            channels=1,
            dtype="int16",
            blocksize=1600,
            callback=receive,
        )
        try:
            self.levels.last_block = time.monotonic()
            self.stream.start()
            event("audio.started", mode="handsfree", threshold=self.prefs.silence_threshold)
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
            self.levels.check_callbacks()
            try:
                return self.blocks.get(timeout=0.1)
            except queue.Empty:
                pass
        return None

    def __exit__(self, *_):
        self.closed.set()
        try:
            if self.stream:
                try:
                    self.stream.abort()
                finally:
                    self.stream.close()
        finally:
            self.discard()
            self.levels.report(
                "audio.stopped",
                mode="handsfree",
                dropped_blocks=self.dropped,
                failed=bool(self.error),
            )


def utterance(
    source,
    stop,
    *,
    threshold,
    deadline,
    require_quiet=False,
    max_seconds=8,
    require_complete=False,
    end_pause=0.5,
    gate=None,
    observe=None,
):
    """Consume one utterance, requiring fresh speech after any boundary reset."""
    audio = bytearray()
    # Keep quiet context and weak consonants before the threshold crossing.
    # Dropping these made short wake words depend on callback alignment and
    # left the recognizer with an artificially abrupt start. This is transient
    # memory, scoped to this one utterance, never a saved microphone recording.
    preceding = bytearray(PRE_ROLL_SAMPLES * 2)
    audio_limit = min(int(max_seconds * RATE) + PRE_ROLL_SAMPLES, MAX_AUDIO_SECONDS * RATE) * 2
    quiet_blocks = max(1, math.ceil(end_pause / BLOCK_SECONDS))
    voiced = quiet = 0
    armed = not require_quiet
    started = time.monotonic()
    complete = False
    while not stop.is_set():
        block = source.read(stop, deadline)
        if block is None:
            break
        level = speech_level(block, remove_dc=gate is not None)
        speaking = gate.observe(level, recording=bool(audio)) if gate else level >= threshold
        if gate and not gate.ready:
            if observe:
                observe("calibrating", level, gate.floor, gate.start)
            preceding.extend(block)
            del preceding[: -PRE_ROLL_SAMPLES * 2]
            continue
        if observe:
            observe(
                "waiting_quiet"
                if not armed
                else "speech"
                if speaking
                else "finishing"
                if audio
                else "listening",
                level,
                gate.floor if gate else 0,
                gate.start if gate else threshold,
            )
        if not armed:
            preceding.extend(block)
            del preceding[: -PRE_ROLL_SAMPLES * 2]
            quiet = 0 if speaking else quiet + 1
            if quiet >= 3:
                armed, quiet = True, 0
            continue
        if speaking:
            voiced += 1
            quiet = 0
        elif audio:
            quiet += 1
        if speaking and not audio:
            audio.extend(preceding)
            preceding.clear()
        if speaking or audio:
            audio.extend(block)
        else:
            preceding.extend(block)
            del preceding[: -PRE_ROLL_SAMPLES * 2]
        if quiet >= quiet_blocks:
            complete = True
            break
        if len(audio) >= audio_limit:
            break
    result = (
        bytes(audio[:audio_limit])
        if not stop.is_set() and voiced >= 2 and (complete or not require_complete)
        else b""
    )
    event(
        "handsfree.utterance",
        samples=len(result) // 2,
        voiced_blocks=voiced,
        preroll_samples=PRE_ROLL_SAMPLES if result else 0,
        elapsed=round(time.monotonic() - started, 3),
        cancelled=stop.is_set(),
        complete=complete,
        noise_floor=round(gate.floor, 6) if gate else 0,
        effective_threshold=round(gate.start, 6) if gate else threshold,
    )
    if gate and audio and not complete:
        # A fan/AGC change or continuous loud audio must not freeze the baseline
        # forever. Discard this incomplete clip and recalibrate on the next one.
        gate.reset()
    return result


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
