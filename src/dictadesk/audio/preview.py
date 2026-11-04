# SPDX-License-Identifier: GPL-3.0-only
"""Pause-triggered local previews. They never enter the result or clipboard."""

from array import array
from pathlib import Path
import queue
import tempfile
import wave
from .input import RATE


class PausePreview:
    def __init__(self, pause_seconds=0.8, threshold=0.015):
        self.pause_samples = int(RATE * pause_seconds)
        self.threshold = threshold
        self.audio = bytearray()
        self.silence = 0
        self.dirty = False
        self.pending = queue.Queue(maxsize=1)

    def add(self, block):
        self.audio.extend(block)
        peak = max((abs(v) for v in array("h", block)), default=0) / 32768
        if peak >= self.threshold:
            self.silence = 0
            self.dirty = True
        else:
            self.silence += len(block) // 2
        if self.dirty and self.silence >= self.pause_samples and len(self.audio) >= RATE:
            # Retain only the newest snapshot when recognition is slower than speech.
            try:
                self.pending.get_nowait()
            except queue.Empty:
                pass
            self.pending.put_nowait(bytes(self.audio))
            self.dirty = False

    def recognize_until_finished(self, capture, recognizer, cancelled, callback):
        while not capture.done.is_set() and not cancelled.is_set():
            try:
                data = self.pending.get(timeout=0.1)
            except queue.Empty:
                continue
            if capture.done.is_set() or cancelled.is_set():
                return
            with tempfile.NamedTemporaryFile(
                suffix=".wav", prefix="preview-", dir=capture.directory
            ) as file:
                with wave.open(file, "wb") as audio:
                    audio.setnchannels(1)
                    audio.setsampwidth(2)
                    audio.setframerate(RATE)
                    audio.writeframes(data)
                file.flush()
                try:
                    answer = recognizer.request(
                        {"op": "transcribe", "path": str(Path(file.name))}, cancelled
                    )
                except Exception:
                    # The final operation reports worker failures; previews are optional.
                    return
                if not capture.done.is_set() and not cancelled.is_set():
                    callback(answer.get("text", ""))
