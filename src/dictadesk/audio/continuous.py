# SPDX-License-Identifier: GPL-3.0-only
"""Bounded, ordered phrase capture; finished phrases are never revised."""

from array import array
from collections import deque
from dataclasses import dataclass
import math
import queue
import threading
import time

from .input import Capture, RATE, resolve


@dataclass(frozen=True)
class AudioUpdate:
    segment: int
    audio: bytes
    final: bool


class Phrases:
    def __init__(self, pause_seconds, threshold, max_seconds=8):
        self.pause = int(pause_seconds * RATE)
        self.threshold = threshold
        self.maximum = int(max_seconds * RATE) * 2
        self.audio = bytearray()
        self.silence = 0
        self.voiced = False

    def add(self, data):
        samples = array("h", data)
        # A single click must not turn an otherwise quiet block into speech.
        rms = math.sqrt(sum(x * x for x in samples) / max(1, len(samples))) / 32768
        if rms >= self.threshold:
            self.voiced = True
            self.silence = 0
        else:
            self.silence += len(data) // 2
        if self.voiced:
            self.audio.extend(data)
        if self.voiced and (self.silence >= self.pause or len(self.audio) >= self.maximum):
            return self.finish()
        return None

    def finish(self):
        data = bytes(self.audio) if self.voiced and len(self.audio) >= RATE // 5 * 2 else None
        self.audio.clear()
        self.voiced = False
        self.silence = 0
        return data


class ContinuousCapture(Capture):
    def __init__(self, prefs, directory):
        super().__init__(prefs, directory, seconds=prefs.continuous_minutes * 60)
        self.phrases = queue.Queue(maxsize=8)
        self.problem = ""
        self.timed_out = False
        self.segment = 0

    def has_updates(self):
        return not self.phrases.empty()

    def next_update(self, timeout=0.1):
        data = self.phrases.get(timeout=timeout)
        update = AudioUpdate(self.segment, data, True)
        self.segment += 1
        return update

    def emit(self, data):
        if data:
            while not self.cancel.is_set():
                try:
                    self.phrases.put(data, timeout=0.1)
                    return
                except queue.Full:
                    pass

    def run(self, started):
        import sounddevice as sd

        chunks = queue.Queue(maxsize=100)
        self.splitter = splitter = Phrases(
            self.prefs.pause_seconds, self.prefs.silence_threshold
        )

        def receive(data, frames, timing, status):
            if status:
                self.problem = "Audio was interrupted. Check the last phrase before continuing."
                self.stop.set()
            try:
                # RawInputStream supplies a CFFI buffer, not a bytes sequence.
                block = bytes(data)
                samples = array("h", block)
                self.level = max((abs(v) for v in samples), default=0) / 32768
                self.peak = max(self.peak, self.level)
                chunks.put_nowait(block)
            except queue.Full:
                self.problem = "Recognition could not keep up. Recording stopped; check the last phrase before continuing."
                self.stop.set()
            except Exception:
                # Callback exceptions otherwise disappear on the audio thread.
                self.problem = "Microphone audio could not be read. Recording stopped; completed text is retained."
                self.stop.set()

        try:
            if self.cancel.is_set():
                return
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
                while not self.stop.is_set() and not self.cancel.is_set():
                    if time.monotonic() >= deadline:
                        self.timed_out = True
                        break
                    try:
                        self.emit(splitter.add(chunks.get(timeout=0.1)))
                    except queue.Empty:
                        pass
            while not chunks.empty() and not self.cancel.is_set():
                self.emit(splitter.add(chunks.get_nowait()))
            if not self.cancel.is_set():
                self.emit(splitter.finish())
        except Exception:
            self.problem = "The microphone stopped. Completed text is retained; check the microphone before continuing."
        finally:
            self.done.set()


class IncrementalCapture(ContinuousCapture):
    """Keep final segments in order; coalesce optional previews under backpressure."""

    def __init__(self, prefs, directory):
        super().__init__(prefs, directory)
        self.condition = threading.Condition()
        self.finals = deque()
        self.partial = None
        self.next_preview = int(1.5 * RATE) * 2

    def has_updates(self):
        with self.condition:
            return bool(self.finals or self.partial)

    def next_update(self, timeout=0.1):
        with self.condition:
            if not self.condition.wait_for(lambda: self.finals or self.partial, timeout):
                raise queue.Empty
            if self.finals:
                result = self.finals.popleft()
                self.condition.notify_all()
                return result
            result, self.partial = self.partial, None
            return result

    def emit(self, data):
        with self.condition:
            if data:
                # A final supersedes a pending preview of the same audio.
                self.partial = None
                while len(self.finals) >= 8 and not self.cancel.is_set():
                    self.condition.wait(0.1)
                if self.cancel.is_set():
                    return
                self.finals.append(AudioUpdate(self.segment, data, True))
                self.segment += 1
                self.next_preview = int(1.5 * RATE) * 2
            elif len(self.splitter.audio) >= self.next_preview:
                self.partial = AudioUpdate(self.segment, bytes(self.splitter.audio), False)
                self.next_preview = len(self.splitter.audio) + int(1.5 * RATE) * 2
            self.condition.notify_all()
