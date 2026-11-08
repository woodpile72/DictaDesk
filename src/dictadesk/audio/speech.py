# SPDX-License-Identifier: GPL-3.0-only
"""Optional local speech output, isolated from the recording/recognition worker."""

import base64
from dataclasses import replace
import sys
import threading
import time
from pathlib import Path

from .service import Recognizer
from ..core.diagnostics import event, failure
from ..core.tts import SpeechStore


def speech_worker():
    root = Path(__file__).resolve().parents[2]
    paths = [str(root)] + ([str(root / "vendor")] if (root / "vendor").is_dir() else [])
    code = (
        "import os,sys; " + f"sys.path[:0]={paths!r}; "
        "os.umask(0o077); sys.dont_write_bytecode=True; "
        "from dictadesk.audio.tts_worker import main; main()"
    )
    return Recognizer([sys.executable, "-c", code])


def play(rate, data, stop):
    import sounddevice as sd

    if stop.is_set():
        raise InterruptedError()
    stream = sd.RawOutputStream(samplerate=rate, channels=1, dtype="int16")
    try:
        stream.start()
        # Short writes make Escape/manual dictation interrupt playback promptly.
        chunk = max(2, int(rate * 0.02) * 2)
        for offset in range(0, len(data), chunk):
            if stop.is_set():
                raise InterruptedError()
            stream.write(data[offset : offset + chunk])
    finally:
        try:
            stream.abort() if stop.is_set() else stream.stop()
        finally:
            stream.close()


class SpeechOutput:
    def __init__(self, dispatch, changed, *, store=None, worker=None, output=play):
        self.dispatch, self.changed = dispatch, changed
        self.store = store or SpeechStore()
        self.worker = worker or speech_worker()
        self.output = output
        self.lock = threading.Lock()
        self.signature = None
        self.problem = ""
        self.notice = ""
        self.downloading = False
        self.removing = False
        self.previewing = False
        self.preparing = False
        self.speaking = False
        self.closing = False
        self.download_cancel = threading.Event()
        self.preview_cancel = threading.Event()
        self.prepare_cancel = threading.Event()
        self.download_thread = self.preview_thread = self.prepare_thread = None
        self.remove_thread = None
        self.progress = (0, 0)
        self.retry_at = 0

    def notify(self):
        self.dispatch(self.changed)

    def ready(self, prefs):
        return self.store.ready(prefs.tts_engine, prefs.tts_model)

    def prepare(self, prefs, stop):
        signature = (prefs.tts_engine, prefs.tts_model)
        if self.signature != signature or not self.worker.loaded:
            self.worker.close()
            self.signature = None
            self.worker.request(
                {
                    "op": "load",
                    "engine": prefs.tts_engine,
                    "model": prefs.tts_model,
                    "root": str(self.store.root()),
                },
                stop,
                timeout=30,
            )
            self.signature = signature

    def speak(self, prefs, stop):
        if self.closing or stop.is_set():
            raise InterruptedError()
        if not self.ready(prefs):
            raise ValueError(
                "The selected speech voice is not ready. Download it in Preferences; the acknowledgment beep remains available."
            )
        deadline = time.monotonic() + 5
        while not self.lock.acquire(timeout=0.05):
            if stop.is_set() or self.closing:
                raise InterruptedError()
            if self.downloading or time.monotonic() >= deadline:
                raise TimeoutError(
                    "Speech output is busy. The acknowledgment beep remains available."
                )
        try:
            self.speaking = True
            self.prepare(prefs, stop)
            response = self.worker.request(
                {"op": "synthesize", "text": prefs.tts_phrase}, stop, timeout=15
            )
            rate, encoded = response.get("rate"), response.get("audio")
            if (
                type(rate) is not int
                or not 8000 <= rate <= 48000
                or not isinstance(encoded, str)
                or len(encoded) > 4 * 48000 * 30
            ):
                raise ValueError("The speech worker returned invalid audio.")
            data = base64.b64decode(encoded, validate=True)
            if not data or len(data) % 2 or len(data) > rate * 2 * 30:
                raise ValueError("The speech worker returned invalid audio.")
            self.output(rate, data, stop)
            self.store.note_use()
            self.problem = ""
            event("speech.played", samples=len(data) // 2, rate=rate)
        finally:
            self.speaking = False
            self.lock.release()

    def acknowledge(self, prefs, stop):
        if self.downloading or self.removing:
            return False
        try:
            self.speak(prefs, stop)
            return True
        except InterruptedError:
            raise
        except Exception as exc:
            failure("speech.acknowledgment_failed", exc)
            self.problem = str(exc)
            self.notify()
            return False

    def stop_playback(self):
        self.preview_cancel.set()
        self.prepare_cancel.set()

    def wait_playback(self, cancelled):
        thread = self.preview_thread
        deadline = time.monotonic() + 5
        while thread and thread.is_alive():
            thread.join(0.05)
            if cancelled.is_set():
                raise InterruptedError()
            if thread.is_alive() and time.monotonic() >= deadline:
                raise TimeoutError(
                    "Speech output did not release the audio device. Try recording again."
                )

    def preview(self, prefs, *, suspend=lambda: None, join=lambda: None, finished=lambda: None):
        if self.closing or self.previewing or self.downloading or self.removing:
            return
        self.previewing = True
        self.problem, self.notice = "", "Preparing voice preview…"
        self.preview_cancel = stop = threading.Event()
        prefs = replace(prefs)
        suspend()
        self.notify()

        def work():
            try:
                join()
                self.speak(prefs, stop)
                self.notice = "Voice preview finished."
            except InterruptedError:
                self.notice = "Voice preview stopped."
            except Exception as exc:
                failure("speech.preview_failed", exc)
                self.problem = str(exc)
            finally:
                self.previewing = False
                self.dispatch(finished)
                self.notify()

        self.preview_thread = threading.Thread(target=work, daemon=True)
        self.preview_thread.start()

    def download(self, model):
        if self.closing or self.downloading or self.previewing or self.removing:
            return
        self.stop_playback()
        self.downloading = True
        self.problem, self.notice = "", "Downloading optional speech files…"
        self.progress = (0, 0)
        self.download_cancel = cancel = threading.Event()
        self.notify()

        def work():
            try:
                with self.lock:
                    self.worker.close()
                    self.signature = None

                    def progress(done, total):
                        self.progress = done, total

                    self.store.install(model, cancel, progress)
                self.notice = "Speech engine and voice downloaded. Enable spoken acknowledgment and Save to use them."
            except InterruptedError:
                self.notice = "Speech download cancelled. Existing voices are unchanged."
            except Exception as exc:
                failure("speech.download_failed", exc)
                self.problem = str(exc)
            finally:
                self.downloading = False
                self.notify()

        self.download_thread = threading.Thread(target=work, daemon=True)
        self.download_thread.start()

    def remove(self):
        if self.downloading or self.previewing or self.preparing or self.speaking:
            return
        self.stop_playback()
        self.removing = True

        def work():
            try:
                with self.lock:
                    self.worker.close()
                    self.signature = None
                    self.store.remove()
                self.notice = (
                    "Optional speech downloads removed. Dictation models are unchanged."
                )
                self.problem = ""
            except Exception as exc:
                self.problem = str(exc)
                failure("speech.remove_failed", exc)
            finally:
                self.removing = False
                self.notify()

        self.remove_thread = threading.Thread(target=work, daemon=True)
        self.remove_thread.start()
        self.notify()

    def tick(self, prefs, *, busy=False):
        if (
            self.closing
            or self.downloading
            or self.previewing
            or self.preparing
            or self.removing
        ):
            return
        active = prefs.tts_enabled and prefs.backend == "local" and prefs.hands_free
        if not active or not self.ready(prefs):
            if self.worker.loaded:
                self.preparing = True

                def unload():
                    try:
                        with self.lock:
                            self.worker.close()
                            self.signature = None
                    except Exception as exc:
                        failure("speech.unload_failed", exc)
                    finally:
                        self.signature = None
                        self.preparing = False
                        self.notify()

                self.prepare_thread = threading.Thread(target=unload, daemon=True)
                self.prepare_thread.start()
            return
        if busy or time.monotonic() < self.retry_at:
            return
        if self.worker.loaded and self.signature == (prefs.tts_engine, prefs.tts_model):
            return
        self.preparing = True
        self.prepare_cancel = stop = threading.Event()
        prefs = replace(prefs)

        def work():
            try:
                with self.lock:
                    self.prepare(prefs, stop)
                self.problem = ""
            except InterruptedError:
                pass
            except Exception as exc:
                failure("speech.prepare_failed", exc)
                self.problem = str(exc)
                self.retry_at = time.monotonic() + 30
            finally:
                self.preparing = False
                self.notify()

        self.prepare_thread = threading.Thread(target=work, daemon=True)
        self.prepare_thread.start()

    def close(self, done):
        self.closing = True
        self.stop_playback()
        self.download_cancel.set()

        def cleanup():
            try:
                try:
                    self.worker.close()
                except Exception as exc:
                    failure("speech.shutdown_worker_failed", exc)
                for thread in (
                    self.preview_thread,
                    self.prepare_thread,
                    self.download_thread,
                    self.remove_thread,
                ):
                    if thread and thread is not threading.current_thread():
                        thread.join(timeout=12)
            finally:
                self.dispatch(done)

        threading.Thread(target=cleanup, daemon=True).start()
