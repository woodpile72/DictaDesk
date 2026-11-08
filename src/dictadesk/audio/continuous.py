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
from ..core.diagnostics import event, failure


@dataclass(frozen=True)
class AudioUpdate:
    segment: int
    audio: bytes
    final: bool
    paused: bool = False
    sentence_end: bool = True


class Phrases:
    def __init__(self, pause_seconds, threshold, max_seconds=8, defer_limit_during_pause=False):
        self.pause = int(pause_seconds * RATE)
        self.threshold = threshold
        self.maximum = int(max_seconds * RATE) * 2
        self.defer_limit_during_pause = defer_limit_during_pause
        self.audio = bytearray()
        self.silence = 0
        self.voiced = False
        self.sentence_end = True

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
        at_limit = len(self.audio) >= self.maximum and (
            not self.defer_limit_during_pause or self.silence == 0
        )
        if self.voiced and (self.silence >= self.pause or at_limit):
            self.sentence_end = self.silence >= self.pause
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
        self.pause_emitted = False
        self.silence_stop_seconds = 0
        self.silence_stopped = False
        self.silent_frames = 0

    def has_updates(self):
        return not self.phrases.empty()

    def next_update(self, timeout=0.1):
        data = self.phrases.get(timeout=timeout)
        if isinstance(data, AudioUpdate):
            return data
        update = AudioUpdate(self.segment, data, True)
        self.segment += 1
        return update

    def pause_update(self):
        splitter = getattr(self, "splitter", None)
        if splitter is None:
            return None
        if splitter.silence == 0:
            self.pause_emitted = False
        if (
            splitter.voiced
            and not self.pause_emitted
            and splitter.silence >= self.prefs.pause_seconds * RATE
            and len(splitter.audio) >= RATE // 5 * 2
        ):
            self.pause_emitted = True
            return AudioUpdate(self.segment, bytes(splitter.audio), False, paused=True)
        return None

    def emit(self, data, *, sentence_end=True):
        if data:
            update = AudioUpdate(self.segment, data, True, sentence_end=sentence_end)
            self.segment += 1
            self.pause_emitted = False
        else:
            update = self.pause_update()
        if update:
            event(
                "audio.segment",
                segment=update.segment,
                samples=len(update.audio) // 2,
                final=update.final,
                paused=update.paused,
            )
            while not self.cancel.is_set():
                try:
                    self.phrases.put(update, timeout=0.1)
                    return
                except queue.Full:
                    pass

    def run(self, started):
        import sounddevice as sd

        chunks = queue.Queue(maxsize=100)
        self.splitter = splitter = Phrases(
            max(self.prefs.pause_seconds, self.prefs.sentence_pause_seconds),
            self.prefs.silence_threshold,
            # Keep the established short recognition windows. Longer windows
            # can revise speech already inserted, even far behind the tail.
            # A pending sentence pause may finish before resetting context.
            max_seconds=8,
            defer_limit_during_pause=True,
        )
        pending_limit = None

        def finish_limit():
            nonlocal pending_limit
            if pending_limit is not None:
                # Finalize the same segment's withheld punctuation if silence
                # or Stop follows a capacity boundary without any more speech.
                self.segment -= 1
                self.emit(pending_limit)
                pending_limit = None

        def consume(block):
            nonlocal pending_limit
            data = splitter.add(block)
            self.emit(data, sentence_end=splitter.sentence_end)
            if data:
                pending_limit = data if not splitter.sentence_end else None
            elif splitter.voiced:
                pending_limit = None
            elif splitter.silence >= splitter.pause:
                finish_limit()

        def receive(data, frames, timing, status):
            if status:
                self.problem = "Audio was interrupted. Check the last phrase before continuing."
                self.stop.set()
            try:
                # RawInputStream supplies a CFFI buffer, not a bytes sequence.
                block = bytes(data)
                self.level, rms, samples = self.levels.observe(block)
                self.peak = max(self.peak, self.level)
                chunks.put_nowait(block)
                if self.silence_stop_seconds:
                    # Count microphone time independently of phrase resets and
                    # inference speed. Use the same noise threshold as splitting.
                    self.silent_frames = (
                        0
                        if rms >= self.prefs.silence_threshold
                        else self.silent_frames + samples
                    )
                    if self.silent_frames >= self.silence_stop_seconds * RATE:
                        self.silence_stopped = True
                        self.stop.set()
            except queue.Full:
                self.problem = "Recognition could not keep up. Recording stopped; check the last phrase before continuing."
                self.stop.set()
            except Exception as exc:
                failure("audio.callback_failed", exc)
                # Callback exceptions otherwise disappear on the audio thread.
                self.problem = "Microphone audio could not be read. Recording stopped; completed text is retained."
                self.stop.set()

        try:
            if self.cancel.is_set():
                return
            self.levels.last_block = time.monotonic()
            with sd.RawInputStream(
                device=resolve(self.prefs),
                samplerate=RATE,
                channels=1,
                dtype="int16",
                blocksize=1600,
                callback=receive,
            ):
                event(
                    "audio.started",
                    mode="continuous",
                    limit=self.limit,
                    threshold=self.prefs.silence_threshold,
                )
                started()
                deadline = time.monotonic() + self.limit
                while not self.stop.is_set() and not self.cancel.is_set():
                    self.levels.check_callbacks()
                    if time.monotonic() >= deadline:
                        self.timed_out = True
                        break
                    try:
                        consume(chunks.get(timeout=0.1))
                    except queue.Empty:
                        pass
            while not chunks.empty() and not self.cancel.is_set():
                consume(chunks.get_nowait())
            if not self.cancel.is_set():
                self.emit(splitter.finish())
                finish_limit()
        except Exception as exc:
            failure("audio.failed", exc)
            self.problem = "The microphone stopped. Try recording again or test the microphone in Preferences."
        finally:
            self.done.set()
            self.levels.report(
                "audio.stopped",
                mode="continuous",
                cancelled=self.cancel.is_set(),
                failed=bool(self.problem),
                timed_out=self.timed_out,
            )
            if not self.levels.voiced and self.levels.samples:
                event("audio.below_threshold", threshold=self.prefs.silence_threshold)


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

    def emit(self, data, *, sentence_end=True):
        with self.condition:
            if data:
                # A final supersedes a pending preview of the same audio.
                self.partial = None
                while len(self.finals) >= 8 and not self.cancel.is_set():
                    self.condition.wait(0.1)
                if self.cancel.is_set():
                    return
                self.finals.append(
                    AudioUpdate(self.segment, data, True, sentence_end=sentence_end)
                )
                self.segment += 1
                self.pause_emitted = False
                self.next_preview = int(1.5 * RATE) * 2
            else:
                paused = self.pause_update()
                if paused:
                    self.partial = paused
                elif not self.pause_emitted and len(self.splitter.audio) >= self.next_preview:
                    self.partial = AudioUpdate(self.segment, bytes(self.splitter.audio), False)
                    self.next_preview = len(self.splitter.audio) + int(1.5 * RATE) * 2
            self.condition.notify_all()
