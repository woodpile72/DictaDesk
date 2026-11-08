# SPDX-License-Identifier: GPL-3.0-only
"""One operation at a time; cancellation invalidates delivery, never the last result."""

from dataclasses import replace
import threading
import time
import queue
import tempfile
import wave

from ..audio.input import Capture, check, microphones
from ..audio.service import Recognizer, compute_capabilities
from .credentials import read_key
from .models import LOCAL_MODELS, downloaded, model_directory, remove_downloads
from .settings import SettingsStore, directory
from .transcript import Transcript, continuation
from .recordings import cleanup_abandoned, workspace


class Session:
    def __init__(self, dispatch, changed, delivered, store=None, recognizer=None):
        self.dispatch, self.changed, self.delivered = dispatch, changed, delivered
        self.store = store or SettingsStore()
        self.preferences = self.store.read()
        self.recognizer = recognizer or Recognizer()
        self.result = Transcript()
        self.last_dictation = ""
        self.phase = "idle"
        self.handsfree = None
        self.desktop_blocked = False
        self.desktop_locked = False
        self.desktop_reason = ""
        self.notice = self.store.problem
        self.problem = None
        self.problem_serial = 0
        self.microphone_problem = "Checking microphone…"
        self.devices = []
        self.device_scan = 0
        self.credential_available = False
        self.capture = None
        self.cancelled = threading.Event()
        self.operation = 0
        self.recording_event = lambda kind, body: None
        self._dictation_operation = None
        self._recording_events = set()
        self._completion_body = "Your dictation is complete."
        self.preview = ""
        self.sample_text = ""
        self.sample_peak = 0.0
        self.last_used = time.monotonic()
        self.thread = None
        self.closing = False
        self.recordings_checked = False
        self.continuous = False
        self.pause_dictation = False
        self.pending_text = ""
        self.stream_delivered = lambda value, target, done: done(False)

    @property
    def busy(self):
        return self.phase != "idle"

    @property
    def model_needed(self):
        return self.preferences.backend == "local" and not self.recognizer.loaded

    @property
    def can_record(self):
        return not (
            self.busy
            or self.desktop_locked
            or self.closing
            or self.store.problem
            or self.microphone_problem
            or self.model_needed
            or (self.preferences.backend == "openai" and not self.credential_available)
        )

    @property
    def state(self):
        if self.desktop_locked:
            return "Standby"
        if self.phase == "listening":
            return "Listening"
        if self.phase in ("transcribing", "delivering", "cancelling"):
            return "Transcribing"
        if self.phase == "loading":
            return "Loading"
        if (
            not self.busy
            and not self.closing
            and not self.store.problem
            and not self.microphone_problem
            and self.model_needed
            and downloaded(self.preferences.quantization, self.preferences.local_model)
        ):
            return "Standby"
        return "Ready" if self.can_record else "Setup needed"

    def scan_devices(self):
        self.device_scan += 1
        generation = self.device_scan
        prefs = self.preferences
        clean_recordings = not self.recordings_checked
        self.recordings_checked = True

        def scan():
            devices, problem, credential = [], "", False
            cleanup_problem = ""
            if clean_recordings:
                try:
                    cleanup_abandoned(directory("CACHE") / "recordings")
                except (OSError, ValueError):
                    cleanup_problem = "Could not clean up old temporary recordings. Check the recording cache permissions."
            try:
                devices = microphones()
                check(prefs)
            except Exception as exc:
                problem = "Microphone unavailable: " + str(exc)
            if prefs.backend == "openai":
                try:
                    credential = bool(read_key())
                except Exception:
                    pass

            def finish():
                if generation == self.device_scan and not self.closing:
                    self.devices, self.microphone_problem, self.credential_available = (
                        devices,
                        problem,
                        credential,
                    )
                    if cleanup_problem and not self.notice:
                        self.set_problem("storage", cleanup_problem)
                    self.changed()

            self.dispatch(finish)

        threading.Thread(target=scan, daemon=True).start()

    def _recording_event(self, generation, kind, body=""):
        if generation != self.operation or generation != self._dictation_operation:
            return
        if kind in self._recording_events:
            return
        terminal = {"completed", "attention", "failed", "cancelled"}
        if kind in terminal and terminal.intersection(self._recording_events):
            return
        if kind != "started" and "started" not in self._recording_events:
            return
        if kind in ("completed", "attention", "failed", "cancelled"):
            self._recording_event(generation, "stopped")
        self._recording_events.add(kind)
        try:
            self.recording_event(kind, body)
        except Exception:
            # Notification availability must never interrupt dictation or delivery.
            pass

    def set_problem(self, category, reason):
        self.problem_serial += 1
        self.problem = (self.problem_serial, category, reason)
        self.notice = reason

    def _begin(self, phase):
        if self.desktop_locked and phase in ("starting", "delivering"):
            return None
        if self.busy or self.closing or self.store.problem:
            return None
        if self.handsfree:
            self.handsfree.suspend()
        self.operation += 1
        self._dictation_operation = None
        self._recording_events = set()
        self._completion_body = "Your dictation is complete."
        self.cancelled = threading.Event()
        self.phase, self.notice = phase, ""
        self.problem = None
        self.preview = ""
        self.changed()
        return self.operation, self.cancelled

    def _run(self, ticket, task, complete):
        generation, cancel = ticket
        operation_kind = self.phase

        def work():
            try:
                if self.handsfree:
                    self.handsfree.join()
                if cancel.is_set():
                    raise InterruptedError()
                if (
                    self.handsfree
                    and generation == self._dictation_operation
                    and self.preferences.backend == "local"
                    and not self.recognizer.loaded
                ):
                    self.recognizer.request(
                        {
                            "op": "load",
                            "local_only": True,
                            "cache": str(model_directory(create=True)),
                            "precision": self.preferences.quantization,
                            "local_model": self.preferences.local_model,
                            "device": self.preferences.device,
                        },
                        cancel,
                        timeout=900,
                    )
                result, problem = task(cancel, generation), ""
            except InterruptedError:
                result, problem = None, "Cancelled. Nothing was inserted."
            except Exception as exc:
                result, problem = None, str(exc)

            def finish():
                if generation != self.operation:
                    return
                self.phase, self.capture = "idle", None
                self.preview = ""
                self.last_used = time.monotonic()
                self._recording_event(generation, "stopped")
                if cancel.is_set():
                    self.notice = (
                        "Continuous dictation cancelled. Completed text is retained; text already inserted is unchanged."
                        if self.continuous
                        else "Cancelled. Nothing was inserted."
                    )
                    self._recording_event(generation, "cancelled")
                elif problem:
                    self.set_problem(
                        "recording"
                        if generation == self._dictation_operation
                        else operation_kind,
                        problem,
                    )
                    self._recording_event(generation, "failed")
                else:
                    try:
                        complete(result)
                        if generation == self._dictation_operation:
                            self.last_dictation = self.result.text
                    except Exception as exc:
                        self.set_problem(
                            "delivery"
                            if generation == self._dictation_operation
                            else operation_kind,
                            str(exc),
                        )
                        self._recording_event(generation, "failed", self.notice)
                    else:
                        if self.phase != "delivering":
                            self._recording_event(
                                generation,
                                "attention" if self.pending_text else "completed",
                                self._completion_body,
                            )
                self.continuous = False
                self.pause_dictation = False
                if self.handsfree:
                    self.handsfree.resume_at = time.monotonic() + 1
                self.changed()

            self.dispatch(finish)

        self.thread = threading.Thread(target=work, daemon=True)
        self.thread.start()

    def load_available_model(self):
        if (
            self.model_needed
            and not self.busy
            and not self.closing
            and not self.store.problem
            and downloaded(self.preferences.quantization, self.preferences.local_model)
        ):
            self.load(local_only=True)

    def load(self, repair=False, *, local_only=False):
        ticket = self._begin("loading")
        if ticket:

            def work(cancel, generation):
                self.recognizer.close()
                result = self.recognizer.request(
                    {
                        "op": "load",
                        "cache": str(model_directory(create=True)),
                        "precision": self.preferences.quantization,
                        "local_model": self.preferences.local_model,
                        "device": self.preferences.device,
                        "repair": repair,
                        "local_only": local_only,
                    },
                    cancel,
                    timeout=900,
                )
                if result.get("device") == "cpu" and self.preferences.device == "cuda":
                    try:
                        self.store.save(replace(self.preferences, device="cpu"))
                    except OSError:
                        result["settings_warning"] = (
                            "Could not save the CPU preference; this session will use CPU."
                        )
                return result

            def complete(result):
                if result.get("device") == "cpu" and self.preferences.device == "cuda":
                    self.preferences = replace(self.preferences, device="cpu")
                self.notice = " ".join(
                    value
                    for value in (
                        "Local model ready.",
                        result.get("compute_notice"),
                        result.get("settings_warning"),
                    )
                    if value
                )
                if result.get("settings_warning"):
                    self.set_problem("saving", result["settings_warning"])

            self._run(ticket, work, complete)

    def download_model(self, precision, repair=False, *, model=None):
        model = model or self.preferences.local_model
        if model not in LOCAL_MODELS:
            raise ValueError("Choose a supported local model.")
        if precision not in LOCAL_MODELS[model]["sizes"]:
            raise ValueError("Choose a supported model precision.")
        ticket = self._begin("loading")
        if ticket:

            def work(cancel, generation):
                self.recognizer.close()
                try:
                    self.recognizer.request(
                        {
                            "op": "load",
                            "cache": str(model_directory(create=True)),
                            "precision": precision,
                            "local_model": model,
                            "device": "cpu",
                            "repair": repair,
                        },
                        cancel,
                        timeout=900,
                    )
                finally:
                    self.recognizer.close()

            self._run(
                ticket,
                work,
                lambda _: setattr(
                    self,
                    "notice",
                    "Model downloaded. Save your local settings, then Load when ready.",
                ),
            )

    def remove_models(self, model=None):
        ticket = self._begin("removing_models")
        if ticket:

            def work(cancel, generation):
                if model is None or model == self.preferences.local_model:
                    self.recognizer.close()
                remove_downloads(model)

            self._run(
                ticket,
                work,
                lambda _: setattr(
                    self,
                    "notice",
                    f"{LOCAL_MODELS[model]['name']} downloads removed."
                    if model
                    else "Downloaded local models removed.",
                ),
            )

    def unload(self):
        ticket = self._begin("unloading")
        if ticket:
            self._run(
                ticket,
                lambda cancel, generation: self.recognizer.close(),
                lambda _: setattr(self, "notice", "Local model unloaded."),
            )

    def record(self, target=None, sample=False):
        if self.phase == "listening":
            self.finish_recording()
            return
        if self.pending_text and not sample:
            self.notice = "Copy or paste the uninserted text before starting a new recording."
            self.changed()
            return
        if not self.can_record:
            self.notice = (
                self.store.problem
                or self.microphone_problem
                or (
                    "Load the local model first."
                    if self.model_needed
                    else "Configure your transcription backend in Preferences."
                )
            )
            self.changed()
            return
        ticket = self._begin("starting")
        if ticket is None:
            return
        if not sample:
            self._dictation_operation = self.operation
        prefs = self.preferences
        self.capture = capture = Capture(
            prefs, directory("CACHE") / "recordings", seconds=8 if sample else None
        )

        def work(cancel, generation):
            path = None
            preview_thread = None
            if prefs.preview_pauses and prefs.backend == "local" and not sample:
                from ..audio.preview import PausePreview

                capture.previewer = PausePreview(prefs.pause_seconds, prefs.silence_threshold)

                def show_preview(text):
                    def apply():
                        if (
                            self.operation == generation
                            and self.phase == "listening"
                            and not cancel.is_set()
                        ):
                            self.preview = text
                            self.changed()

                    self.dispatch(apply)

                preview_thread = threading.Thread(
                    target=capture.previewer.recognize_until_finished,
                    args=(capture, self.recognizer, cancel, show_preview),
                    daemon=True,
                )
                preview_thread.start()

            def phase(value):
                if self.operation == generation and not cancel.is_set():
                    self.phase = value
                    if value == "listening":
                        self._recording_event(generation, "started")
                    self.changed()

            try:
                try:
                    path = capture.run(lambda: self.dispatch(lambda: phase("listening")))
                finally:
                    self.dispatch(lambda: self._recording_event(generation, "stopped"))
                if preview_thread:
                    preview_thread.join()
                if cancel.is_set():
                    raise InterruptedError()
                self.dispatch(lambda: phase("transcribing"))
                if prefs.backend == "local":
                    payload = {"op": "transcribe", "path": str(path)}
                else:
                    key = read_key()
                    if not key:
                        raise ValueError("Add an OpenAI API key in Preferences.")
                    payload = {
                        "op": "cloud",
                        "path": str(path),
                        "key": key,
                        "model": prefs.cloud_model,
                        "language": prefs.language,
                    }
                answer = self.recognizer.request(payload, cancel)
                text = answer.get("text", "").strip()
                if not text:
                    raise ValueError(
                        "No speech was recognized. Your previous transcript is still available."
                    )
                return text, capture.peak
            finally:
                capture.done.set()
                if preview_thread:
                    preview_thread.join()
                if path:
                    path.unlink(missing_ok=True)

        def complete(value):
            text, peak = value
            if sample:
                sample_result = Transcript()
                sample_result.replace(text, prefs.vocabulary, prefs.spoken_punctuation)
                self.sample_text, self.sample_peak = sample_result.text, peak
                self.notice = "Microphone sample transcribed. Nothing was copied or pasted."
            else:
                self.result.replace(text, prefs.vocabulary, prefs.spoken_punctuation)
                self.pending_text = ""
                self.delivered(self.result.text, target if prefs.auto_paste else None)
                if target is None or not prefs.auto_paste:
                    self._completion_body = "Your text has been copied to the clipboard."

        def record_work(cancel, generation):
            with workspace(directory("CACHE") / "recordings") as path:
                capture.directory = path
                return work(cancel, generation)

        self._run(ticket, record_work, complete)

    def record_continuous(self, target=None, *, pause_mode=False):
        from ..audio.continuous import ContinuousCapture, IncrementalCapture
        from ..audio.input import RATE
        from .incremental import StablePrefix
        from .transcript import explicit_ending, provisional

        if self.continuous and self.phase in ("starting", "listening"):
            self.finish_recording()
            return
        if not self.can_record:
            self.notice = "Prepare the microphone and transcription backend before starting continuous dictation."
            self.changed()
            return
        if self.pending_text:
            self.notice = (
                "Recover the uninserted text before starting another continuous session."
            )
            self.changed()
            return
        ticket = self._begin("starting")
        if not ticket:
            return
        self._dictation_operation = self.operation
        self.continuous = True
        self.pause_dictation = pause_mode
        prefs = self.preferences
        live = prefs.backend == "local" and not pause_mode and prefs.continuous_live
        capture_type = IncrementalCapture if live else ContinuousCapture
        self.capture = capture = capture_type(prefs, directory("CACHE") / "recordings")
        if pause_mode:
            capture.limit = prefs.max_seconds
            capture.silence_stop_seconds = prefs.silence_stop_seconds
        self.changed()
        first = True
        active_segment = None
        base_original = base_corrected = ""

        def work(cancel, generation):
            with workspace(directory("CACHE") / "recordings") as folder:
                capture.directory = folder

                def listening():
                    if generation == self.operation and not cancel.is_set():
                        self.phase = "listening"
                        self._recording_event(generation, "started")
                        self.notice = (
                            "Live dictation: agreed words are inserted while you speak; the changing ending stays in Preview. Press Escape to finish."
                            if live
                            else "Pause between phrases to insert text. Press Escape to finish."
                        )
                        self.changed()

                def record_audio():
                    try:
                        capture.run(lambda: self.dispatch(listening))
                    finally:
                        self.dispatch(lambda: self._recording_event(generation, "stopped"))

                recorder = threading.Thread(target=record_audio, daemon=True)
                recorder.start()
                segment = None
                stable = None
                try:
                    while not capture.done.is_set() or capture.has_updates():
                        if cancel.is_set():
                            raise InterruptedError()
                        try:
                            update = capture.next_update(timeout=0.1)
                        except queue.Empty:
                            continue
                        if segment != update.segment:
                            segment = update.segment
                            # Leave enough context for multiword vocabulary entries.
                            hold = max(
                                [3]
                                + [len(source.split()) + 1 for source, _ in prefs.vocabulary]
                            )
                            stable = StablePrefix(hold_words=hold)
                        with tempfile.NamedTemporaryFile(suffix=".wav", dir=folder) as audio:
                            with wave.open(audio, "wb") as output:
                                output.setnchannels(1)
                                output.setsampwidth(2)
                                output.setframerate(RATE)
                                output.writeframes(update.audio)
                            audio.flush()
                            payload = {"op": "transcribe", "path": audio.name}
                            if prefs.backend == "openai":
                                key = read_key()
                                if not key:
                                    raise ValueError("Add an OpenAI API key in Preferences.")
                                payload.update(
                                    op="cloud",
                                    key=key,
                                    model=prefs.cloud_model,
                                    language=prefs.language,
                                )
                            answer = self.recognizer.request(payload, cancel)
                        text = answer.get("text", "").strip()
                        piece = Transcript()
                        piece.replace(text, prefs.vocabulary, prefs.spoken_punctuation)
                        if not update.final or not update.sentence_end:
                            piece.corrected = provisional(
                                piece.corrected, text, prefs.spoken_punctuation
                            )
                        preview, conflict = stable.update(
                            piece.corrected,
                            final=update.final
                            or (
                                update.paused
                                and explicit_ending(text, prefs.spoken_punctuation)
                            ),
                            paused=update.paused,
                        )
                        committed = stable.committed
                        acknowledged = threading.Event()

                        def accept(
                            text=text,
                            acknowledged=acknowledged,
                            update=update,
                            preview=preview,
                            conflict=conflict,
                            committed=committed,
                            corrected=piece.corrected,
                        ):
                            nonlocal first, active_segment, base_original, base_corrected
                            if generation != self.operation or cancel.is_set():
                                acknowledged.set()
                                return
                            if active_segment != update.segment:
                                active_segment = update.segment
                                base_original = "" if first else self.result.original
                                base_corrected = "" if first else self.result.corrected
                            self.preview = preview if not update.final else ""
                            if conflict:
                                # Preserve the revised hypothesis for manual review.
                                # Never replace text in the destination or auto-paste
                                # an ambiguous suffix after a committed prefix changes.
                                if corrected:
                                    self.result.original = base_original + continuation(
                                        base_original, text
                                    )
                                    self.result.corrected = base_corrected + continuation(
                                        base_corrected, corrected
                                    )
                                self.pending_text = ""
                                self.preview = ""
                                capture.stop.set()
                                acknowledged.set()
                                self.changed()
                                return
                            if not committed:
                                acknowledged.set()
                                self.changed()
                                return
                            if first:
                                self.result = Transcript()
                                first = False
                            combined = base_corrected + continuation(base_corrected, committed)
                            delta = combined[len(self.result.corrected) :]
                            self.result.original = base_original + continuation(
                                base_original, text if update.final else committed
                            )
                            self.result.corrected = combined
                            self.result.undo = False
                            self.pending_text += delta
                            completed = False

                            def done(inserted):
                                nonlocal completed
                                if completed:
                                    return
                                completed = True
                                if inserted and delta and generation == self.operation:
                                    self.pending_text = self.pending_text[: -len(delta)]
                                acknowledged.set()
                                self.changed()

                            self.changed()
                            try:
                                if delta and target and prefs.auto_paste:
                                    self.stream_delivered(delta, target, done)
                                else:
                                    done(False)
                            except Exception:
                                done(False)

                        self.dispatch(accept)
                        while not acknowledged.wait(0.1):
                            if cancel.is_set():
                                capture.cancel.set()
                        if conflict:
                            raise RuntimeError(
                                "Recognition revised words already committed. Dictation stopped. Review Last Transcript before replacing text in your document; no earlier text was automatically changed."
                            )
                    if capture.problem:
                        raise RuntimeError(
                            capture.problem + " Completed text remains in Last Transcript."
                        )
                    return capture.timed_out
                finally:
                    capture.cancel.set()
                    recorder.join()

        def complete(timed_out):
            if first:
                raise ValueError(
                    "No speech was recognized. Your previous transcript is still available."
                )
            self.notice = ("Recording time limit reached. " if timed_out else "") + (
                "Uninserted text is available to copy or paste." if self.pending_text else ""
            )

            if self.pending_text:
                self.delivered(self.pending_text, target if prefs.auto_paste else None)
                if not prefs.auto_paste:
                    self._completion_body = "Your text has been copied to the clipboard."

        self._run(ticket, work, complete)

    def finish_recording(self):
        if self.capture:
            self.capture.stop.set()
            self.notice = "Finishing recording…"
            self.changed()

    def cancel(self):
        if not self.busy:
            return
        self.cancelled.set()
        if self.capture:
            self.capture.cancel.set()
        self.phase = "cancelling"
        self.changed()

    def save_preferences(self, prefs, success, prepare=lambda: None):
        prefs.validate()
        ticket = self._begin("saving")
        if ticket:
            previous = self.preferences

            def work(cancel, generation):
                nonlocal prefs
                compute_notice = ""
                if prefs.device == "cuda":
                    capability = compute_capabilities(cancel)
                    if not capability["cuda"]:
                        prefs = replace(prefs, device="cpu")
                        compute_notice = capability["reason"] + " Switched to CPU."
                # The disk transaction completes even if the app is closing.
                prepare()
                self.store.save(prefs)
                if (prefs.backend, prefs.local_model, prefs.quantization, prefs.device) != (
                    previous.backend,
                    previous.local_model,
                    previous.quantization,
                    previous.device,
                ):
                    self.recognizer.close()
                return compute_notice

            def complete(compute_notice):
                self.preferences = prefs
                self.notice = "Preferences saved." + (
                    " " + compute_notice if compute_notice else ""
                )
                success()
                self.scan_devices()

            self._run(ticket, work, complete)

    def tick(self):
        if (
            self.continuous
            and self.capture
            and self.capture.done.is_set()
            and self.phase == "listening"
        ):
            self.phase = "transcribing"
            self.changed()
        process = getattr(self.recognizer, "process", None)
        if self.recognizer.loaded and process is not None and process.poll() is not None:
            self.recognizer.loaded = False
            self.set_problem(
                "loading", "The speech worker stopped. Start dictation to load it again."
            )
            self.changed()
        if (
            not self.busy
            and self.recognizer.loaded
            and not (self.handsfree and self.handsfree.enabled)
            and self.preferences.idle_seconds
            and time.monotonic() - self.last_used >= self.preferences.idle_seconds
        ):
            self.unload()

    def close(self, done):
        self.closing = True
        if self.handsfree:
            self.handsfree.suspend()
        if self.busy:
            self.cancel()
        thread = self.thread

        def cleanup():
            if self.handsfree:
                self.handsfree.join()
            self.recognizer.close()
            if thread and thread.is_alive():
                thread.join()
            self.dispatch(done)

        threading.Thread(target=cleanup, daemon=True).start()
