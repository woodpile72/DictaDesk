# SPDX-License-Identifier: GPL-3.0-only
"""One operation at a time; every recording owns fresh, disposable state."""

from dataclasses import replace
import threading
import time
import queue
import tempfile
import wave

from ..audio.input import Capture, check, microphones
from ..audio.service import Recognizer, compute_capabilities
from .diagnostics import event, failure
from .credentials import read_key
from .models import LOCAL_MODELS, downloaded, model_directory, remove_downloads
from .settings import SettingsStore, directory
from .transcript import Transcript, continuation, has_content
from .recordings import cleanup_abandoned, workspace

INSERTION_TIMEOUT = 8


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
        self.device_checks = []
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
        self.clipboard_fallback = lambda value: self.delivered(value, None)
        self.stream_delivered = lambda value, target, done: done(False)

    @property
    def busy(self):
        return self.phase != "idle"

    @property
    def model_needed(self):
        return self.preferences.backend == "local" and not self.recognizer.loaded

    @property
    def can_sample(self):
        # Selected sample settings are checked and prepared in the worker.
        return not (self.busy or self.desktop_locked or self.closing or self.store.problem)

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
        if self.closing:
            return
        native_finished = threading.Event()
        self.device_checks = [event for event in self.device_checks if not event.is_set()]
        self.device_checks.append(native_finished)
        self.device_scan += 1
        generation = self.device_scan
        prefs = self.preferences
        clean_recordings = not self.recordings_checked
        self.recordings_checked = True

        def scan():
            devices, problem, credential = [], "", False
            cleanup_problem = ""
            try:
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
            finally:
                native_finished.set()
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
        event("recording.event", operation=generation, kind=kind, phase=self.phase)
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
        event("session.begin", operation=self.operation, phase=phase)
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
                failure(
                    "session.worker_failed", exc, operation=generation, phase=operation_kind
                )
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
                        "Dictation cancelled. Text already inserted is unchanged."
                        if self.continuous
                        else "Cancelled. Nothing was inserted."
                    )
                    self._recording_event(generation, "cancelled")
                elif problem:
                    self.copy_failed_dictation(generation)
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
                        failure("session.completion_failed", exc, operation=generation)
                        self.phase = "idle"
                        if generation == self._dictation_operation and has_content(
                            self.result.text
                        ):
                            self.last_dictation = self.result.text
                        self.copy_failed_dictation(generation)
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
                                "completed",
                                self._completion_body,
                            )
                event(
                    "session.finished",
                    operation=generation,
                    phase=self.phase,
                    cancelled=cancel.is_set(),
                    failed=bool(problem or self.problem),
                )
                self.continuous = False
                self.pause_dictation = False
                if self.handsfree:
                    self.handsfree.resume_at = time.monotonic() + 1
                self.changed()

            self.dispatch(finish)

        self.thread = threading.Thread(target=work, daemon=True)
        self.thread.start()

    def copy_failed_dictation(self, generation):
        if generation == self._dictation_operation and has_content(self.result.text):
            try:
                self.clipboard_fallback(self.result.text)
                event(
                    "delivery.clipboard_fallback",
                    operation=generation,
                    characters=len(self.result.text),
                )
            except Exception as exc:
                failure("delivery.clipboard_failed", exc, operation=generation)

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
                    "Model downloaded. Start dictation or test the microphone to load it automatically. Save any changed model choices before dictating.",
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

    def record(self, target=None, sample=False, *, preferences=None, api_key=None):
        if self.phase == "listening":
            self.finish_recording()
            return
        if not (self.can_sample if sample else self.can_record):
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
            self.result = Transcript()
        prefs = replace(preferences or self.preferences) if sample else self.preferences
        event(
            "recording.requested",
            operation=self.operation,
            mode="sample" if sample else "single",
            backend=prefs.backend,
            model=prefs.local_model,
            precision=prefs.quantization,
            device=prefs.device,
            microphone_default=not bool(prefs.microphone),
        )

        def variant(value):
            return value.local_model, value.quantization, value.device

        temporary_worker = (
            sample and prefs.backend == "local" and variant(prefs) != variant(self.preferences)
        )
        worker = Recognizer() if temporary_worker else self.recognizer
        if sample:
            self.sample_text, self.sample_peak = "", 0.0
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
                selected_key = None
                if sample:
                    prefs.validate()
                    for native_finished in tuple(self.device_checks):
                        while not native_finished.wait(0.05):
                            if cancel.is_set():
                                raise InterruptedError()
                    check(prefs)
                    if prefs.backend == "local" and not worker.loaded:
                        if not downloaded(prefs.quantization, prefs.local_model):
                            raise ValueError(
                                "Download the selected speech model before testing the microphone."
                            )
                        self.dispatch(lambda: phase("loading"))
                        worker.request(
                            {
                                "op": "load",
                                "local_only": True,
                                "cache": str(model_directory(create=True)),
                                "precision": prefs.quantization,
                                "local_model": prefs.local_model,
                                "device": prefs.device,
                            },
                            cancel,
                            timeout=900,
                        )
                        self.dispatch(lambda: phase("starting"))
                    elif prefs.backend == "openai":
                        selected_key = api_key or read_key()
                        if not selected_key:
                            raise ValueError(
                                "Add an API key in Preferences before recording a remote sample."
                            )
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
                    key = selected_key if sample else read_key()
                    if not key:
                        raise ValueError("Add an OpenAI API key in Preferences.")
                    payload = {
                        "op": "cloud",
                        "path": str(path),
                        "key": key,
                        "model": prefs.cloud_model,
                        "language": prefs.language,
                    }
                answer = worker.request(payload, cancel)
                text = answer.get("text", "").strip()
                if not text:
                    raise ValueError(
                        "No speech was recognized. Try again or test the microphone in Preferences."
                    )
                return text, capture.peak
            finally:
                capture.done.set()
                if preview_thread:
                    preview_thread.join()
                if path:
                    path.unlink(missing_ok=True)
                if temporary_worker:
                    worker.close()

        def complete(value):
            text, peak = value
            if sample:
                sample_result = Transcript()
                sample_result.replace(text, prefs.vocabulary, prefs.spoken_punctuation)
                self.sample_text, self.sample_peak = sample_result.text, peak
                self.notice = "Microphone sample transcribed. Nothing was copied or pasted."
            else:
                self.result.replace(text, prefs.vocabulary, prefs.spoken_punctuation)
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
        ticket = self._begin("starting")
        if not ticket:
            return
        self._dictation_operation = self.operation
        self.result = Transcript()
        self.continuous = True
        self.pause_dictation = pause_mode
        prefs = self.preferences
        live = prefs.backend == "local" and not pause_mode and prefs.continuous_live
        event(
            "recording.requested",
            operation=self.operation,
            mode="standard" if pause_mode else "extended",
            live=live,
            backend=prefs.backend,
            model=prefs.local_model,
            precision=prefs.quantization,
            device=prefs.device,
            microphone_default=not bool(prefs.microphone),
        )
        capture_type = IncrementalCapture if live else ContinuousCapture
        self.capture = capture = capture_type(prefs, directory("CACHE") / "recordings")
        if pause_mode:
            capture.limit = prefs.max_seconds
            capture.silence_stop_seconds = prefs.silence_stop_seconds
        self.changed()
        first = True
        inserted_length = 0
        deferred = False
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
                    except Exception as exc:
                        failure("session.capture_failed", exc, operation=generation)
                        capture.problem = (
                            "The microphone stopped unexpectedly. Try recording again."
                        )
                    finally:
                        capture.done.set()
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
                            nonlocal inserted_length, deferred
                            if (
                                generation != self.operation
                                or cancel.is_set()
                                or acknowledged.is_set()
                            ):
                                acknowledged.set()
                                return
                            if active_segment != update.segment:
                                active_segment = update.segment
                                base_original = "" if first else self.result.original
                                base_corrected = "" if first else self.result.corrected
                            self.preview = preview if not update.final else ""
                            if conflict:
                                # Copy the revised hypothesis on failure. Never
                                # replace text already inserted with a new hypothesis.
                                if corrected:
                                    self.result.original = base_original + continuation(
                                        base_original, text
                                    )
                                    self.result.corrected = base_corrected + continuation(
                                        base_corrected, corrected
                                    )
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
                            if not delta:
                                acknowledged.set()
                                self.changed()
                                return
                            completed = False

                            def done(inserted):
                                nonlocal completed, inserted_length, deferred
                                if completed or acknowledged.is_set():
                                    return
                                completed = True
                                if generation == self.operation:
                                    if inserted:
                                        inserted_length += len(delta)
                                    else:
                                        deferred = True
                                acknowledged.set()
                                self.changed()

                            self.changed()
                            try:
                                if delta and target and prefs.auto_paste and not deferred:
                                    self.stream_delivered(delta, target, done)
                                else:
                                    done(False)
                            except Exception:
                                done(False)

                        self.dispatch(accept)
                        deadline = time.monotonic() + INSERTION_TIMEOUT
                        while not acknowledged.wait(0.1):
                            if cancel.is_set():
                                acknowledged.set()
                                raise InterruptedError()
                            if time.monotonic() >= deadline:
                                acknowledged.set()
                                raise TimeoutError(
                                    "Text insertion timed out. Try recording again."
                                )
                        if conflict:
                            raise RuntimeError(
                                "Recognition revised words already inserted. Dictation stopped; the revised transcript was copied to the clipboard. Previously inserted text is unchanged."
                            )
                    if capture.problem:
                        raise RuntimeError(capture.problem)
                    return capture.timed_out
                finally:
                    capture.cancel.set()
                    recorder.join()

        def complete(timed_out):
            if first:
                if capture.levels.samples and not capture.levels.voiced:
                    raise ValueError(
                        "No speech reached recognition: captured audio stayed below the Silence Threshold. Lower that threshold or increase microphone input volume in Preferences, then try again."
                    )
                raise ValueError(
                    "No speech was recognized. Try again or test the microphone in Preferences."
                )
            self.notice = "Recording time limit reached." if timed_out else ""
            # Only this recording's transcript and acknowledged prefix are used.
            # Failed delivery never creates persistent pending/recovery state.
            remaining = self.result.text[inserted_length:]
            if remaining:
                self.delivered(remaining, target if prefs.auto_paste else None)
                if not prefs.auto_paste:
                    self._completion_body = "Your text has been copied to the clipboard."

        self._run(ticket, work, complete)

    def finish_recording(self):
        event("session.stop_requested", operation=self.operation, phase=self.phase)
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
        event("session.cancel_requested", operation=self.operation, phase=self.phase)
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
        device_checks = tuple(self.device_checks)

        def cleanup():
            # PortAudio teardown must not race an in-flight device/settings call.
            # Credential probes run after this native section and need not delay exit.
            for native_finished in device_checks:
                native_finished.wait()
            if self.handsfree:
                try:
                    self.handsfree.join()
                except Exception as exc:
                    failure("shutdown.listener_failed", exc)
            try:
                self.recognizer.close()
            except Exception as exc:
                failure("shutdown.worker_failed", exc)
            if thread and thread.is_alive():
                thread.join(timeout=5)
                if thread.is_alive():
                    event("shutdown.operation_timeout", operation=self.operation)
            self.dispatch(done)

        threading.Thread(target=cleanup, daemon=True).start()
