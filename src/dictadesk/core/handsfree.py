# SPDX-License-Identifier: GPL-3.0-only
"""Local-only hands-free coordinator; epochs invalidate every deferred action."""

import base64
from dataclasses import replace
import threading
import time
from datetime import datetime, timedelta

from ..audio.handsfree import AdaptiveGate, MemoryInput, acknowledge, utterance
from .voice_commands import wake_activation, command, choice_number
from .diagnostics import event, failure


class HandsFree:
    def __init__(
        self,
        session,
        changed,
        launch,
        feedback,
        *,
        desktop_action=None,
        capture_destination=None,
        source=MemoryInput,
        beep=acknowledge,
        speech_acknowledge=None,
        output_busy=lambda: False,
        wake_detected=lambda inline: None,
    ):
        self.session = session
        self.changed, self.launch, self.feedback = changed, launch, feedback
        self.source, self.beep = source, beep
        self.speech_acknowledge, self.output_busy = speech_acknowledge, output_busy
        self.desktop_action = desktop_action
        self.wake_detected = wake_detected
        self.capture_destination = capture_destination
        self.activation_target = None
        self.pause_until = 0
        self.generation = 0
        self.state = "off"
        self.problem = ""
        self.thread = None
        self.stop = threading.Event()
        self.finished = threading.Event()
        self.signature = None
        self.failed = False
        self.resume_at = 0
        self.model_retry_at = 0
        self.recognitions = 0
        self.override_disabled = False
        self.choice = None
        self.gate = None
        self.activity = ""
        self.last_check = ""

    @property
    def paused(self):
        return bool(self.pause_until and time.monotonic() < self.pause_until)

    def pause(self, minutes=30):
        if type(minutes) is not int or not 1 <= minutes <= 240:
            raise ValueError("Pause voice commands for 1–240 minutes.")
        self.suspend()
        self.pause_until = time.monotonic() + minutes * 60
        self.state = "paused"
        until = (datetime.now() + timedelta(minutes=minutes)).strftime("%H:%M")
        self.problem = f"Voice commands paused until {until}. Use Resume Voice Commands Now in the tray to resume sooner."
        self.changed()

    def resume(self):
        self.suspend()
        self.pause_until = 0
        self.resume_at = 0
        self.problem = ""
        self.changed()

    @property
    def enabled(self):
        p = self.session.preferences
        return p.backend == "local" and p.hands_free and not self.override_disabled

    def valid(self, generation):
        return (
            generation == self.generation
            and self.enabled
            and not self.paused
            and not getattr(self.session, "desktop_blocked", False)
            and not self.stop.is_set()
            and not self.session.busy
            and not self.session.closing
            and not self.output_busy()
        )

    def suspend(self):
        if self.choice:
            self.choice[2].set()
        self.choice = None
        if not self.stop.is_set():
            event("handsfree.suspended", generation=self.generation, state=self.state)
            self.generation += 1
            self.stop.set()
            self.finished.set()
        self.state = "off"
        self.activity = ""
        self.resume_at = time.monotonic() + 1

    def join(self):
        thread = self.thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=20)
            if thread.is_alive():
                raise TimeoutError("Background microphone processing did not stop. Try again.")

    def cancel(self):
        self.suspend()
        self.changed()

    def complete(self, generation):
        if self.valid(generation):
            # Retire the activation as well as its audio: a delayed chooser
            # signal must never be usable by the next command.
            self.suspend()
            self.changed()

    def publish(self, generation, state):
        def update():
            if self.valid(generation):
                self.state = state
                event("handsfree.state", generation=generation, state=state)
                self.changed()

        self.session.dispatch(update)

    def observe_audio(self, generation, stage, level, floor, threshold):
        if generation != self.generation or self.stop.is_set():
            return
        self.activity = stage

    def publish_check(self, generation, matched, has_words, inline):
        def update():
            if self.valid(generation):
                self.last_check = (
                    "Wake phrase detected."
                    if matched
                    else "Speech recognized; wake phrase not found."
                    if has_words
                    else "No words recognized in the last speech."
                )
                self.changed()
                if matched:
                    self.wake_detected(inline)

        self.session.dispatch(update)

    def offer_choices(self, generation, handler):
        if self.valid(generation):
            # A new identity also invalidates recognition from the previous page.
            if self.choice:
                self.choice[2].set()
            self.choice = (generation, handler, threading.Event())
            self.changed()

    def await_action(self, generation, stop, prefs):
        page, deadline = None, 0
        action_deadline = time.monotonic() + 60
        while self.valid(generation) and not self.finished.is_set():
            if time.monotonic() >= action_deadline:
                event("handsfree.action_timeout", generation=generation)
                self.session.dispatch(lambda: self.complete(generation))
                return
            choice = self.choice
            if choice is None:
                stop.wait(0.1)
                continue
            if choice is not page:
                page, deadline = choice, time.monotonic() + 20
            with self.source(prefs) as source:
                data = utterance(
                    source,
                    choice[2],
                    threshold=prefs.silence_threshold,
                    deadline=deadline,
                    gate=self.gate,
                    observe=lambda *values: self.observe_audio(generation, *values),
                    require_complete=True,
                )
            if not self.valid(generation) or self.choice is not choice:
                continue
            if not data and time.monotonic() < deadline:
                continue
            text = self.recognize(data, generation, stop)
            reply = choice_number(text) if data else "timeout"
            delivered = threading.Event()

            def select(choice=choice, text=text, reply=reply, delivered=delivered):
                try:
                    if (
                        self.valid(generation)
                        and self.choice is choice
                        and not delivered.is_set()
                    ):
                        choice[1](reply, text)
                finally:
                    delivered.set()

            self.session.dispatch(select)
            selection_deadline = time.monotonic() + 3
            while self.valid(generation) and not delivered.wait(0.1):
                if time.monotonic() >= selection_deadline:
                    delivered.set()
                    raise TimeoutError("Command selection timed out.")

    def tick(self):
        p = self.session.preferences
        signature = (
            p.backend,
            p.hands_free,
            p.wake_phrase,
            p.command_sound,
            p.local_model,
            p.quantization,
            p.device,
            p.microphone,
            p.host_api,
            p.silence_threshold,
        )
        if signature != self.signature:
            self.suspend()
            self.signature, self.failed = signature, False
            self.model_retry_at = 0
            self.last_check = ""
            if not self.paused:
                self.problem = ""
        if getattr(self.session, "desktop_blocked", False):
            self.suspend()
            self.state = "paused"
            self.problem = self.session.desktop_reason
            return
        if not self.enabled or self.session.busy or self.session.closing or self.output_busy():
            if not self.stop.is_set():
                self.suspend()
            return
        if self.paused:
            self.state = "paused"
            return
        if self.pause_until:
            self.pause_until = 0
            self.resume_at = 0
            self.problem = ""
        if self.thread and self.thread.is_alive():
            return
        if time.monotonic() < self.resume_at:
            return
        if self.failed:
            self.failed = False
        if self.session.microphone_problem or self.session.store.problem:
            self.state, self.problem = (
                "unavailable",
                self.session.microphone_problem or self.session.store.problem,
            )
            return
        if not self.session.recognizer.loaded:
            if time.monotonic() < self.model_retry_at:
                self.state = "unavailable"
                self.problem = "Local model unavailable. Listening will retry automatically; check the model in Preferences."
                return
            self.state = "loading"
            self.model_retry_at = time.monotonic() + 30
            self.session.load_available_model()
            if not self.session.busy:
                self.state = "unavailable"
                self.problem = "Download and load a local model to use hands-free commands."
            return
        self.model_retry_at = 0
        self.problem = ""
        self.generation += 1
        generation = self.generation
        self.stop, self.finished = threading.Event(), threading.Event()
        stop = self.stop
        prefs = replace(p)
        self.thread = threading.Thread(
            target=self.run, args=(generation, stop, prefs), daemon=True
        )
        self.thread.start()

    def recognize(self, data, generation, stop):
        if not data or not self.valid(generation):
            return ""
        self.recognitions += 1
        event("handsfree.recognizing", generation=generation, samples=len(data) // 2)
        # Let an in-flight local inference finish before yielding the shared model.
        # Its result is discarded on suspension; cancellation never unloads it.
        reply = self.session.recognizer.request(
            {"op": "transcribe_pcm", "audio": base64.b64encode(data).decode("ascii")},
            threading.Event(),
            timeout=15,
        )
        return reply.get("text", "") if self.valid(generation) and not stop.is_set() else ""

    def run(self, generation, stop, prefs):
        self.gate = gate = AdaptiveGate(prefs.silence_threshold)
        try:
            while self.valid(generation):
                # Open a fresh stream only after own sounds have finished.
                with self.source(prefs) as source:
                    self.publish(generation, "wake")
                    data = utterance(
                        source,
                        stop,
                        threshold=prefs.silence_threshold,
                        deadline=float("inf"),
                        require_quiet=True,
                        max_seconds=6,
                        require_complete=True,
                        gate=gate,
                        observe=lambda *values: self.observe_audio(generation, *values),
                    )
                # Close capture before inference. Disable/dictation never waits
                # for a model result to release the microphone.
                if not self.valid(generation):
                    return
                self.publish(generation, "processing")
                text = self.recognize(data, generation, stop)
                if not self.valid(generation):
                    return
                match_kind, inline = wake_activation(text, prefs.wake_phrase)
                if data:
                    self.publish_check(generation, bool(match_kind), bool(text), bool(inline))
                event(
                    "handsfree.wake_result",
                    generation=generation,
                    characters=len(text),
                    matched=bool(match_kind),
                    match_kind=match_kind,
                )
                if not match_kind:
                    continue
                if self.capture_destination:
                    captured = threading.Event()

                    def remember():
                        self.activation_target = None
                        try:
                            if self.valid(generation) and not captured.is_set():
                                self.activation_target = self.capture_destination()
                        except Exception:
                            pass  # Commands with a target will fail safely.
                        finally:
                            captured.set()

                    self.session.dispatch(remember)
                    capture_deadline = time.monotonic() + 3
                    while self.valid(generation) and not captured.wait(0.05):
                        if time.monotonic() >= capture_deadline:
                            captured.set()
                            raise TimeoutError("Could not capture the command destination.")
                    if not self.valid(generation):
                        return
                self.publish(generation, "command")
                if inline:
                    text = inline
                else:
                    spoken = False
                    if prefs.tts_enabled and self.speech_acknowledge:
                        try:
                            spoken = self.speech_acknowledge(prefs, stop)
                        except InterruptedError:
                            return
                        except Exception as exc:
                            failure("handsfree.speech_ack_failed", exc)
                    if not spoken and prefs.command_sound:
                        try:
                            self.beep(stop)
                        except Exception:
                            pass  # Missing output must not disable voice commands.
                    if stop.wait(0.4):
                        return
                    deadline = time.monotonic() + 8
                    with self.source(prefs) as source:
                        data = utterance(
                            source,
                            stop,
                            threshold=prefs.silence_threshold,
                            deadline=deadline,
                            require_quiet=True,
                            require_complete=True,
                            gate=gate,
                            observe=lambda *values: self.observe_audio(generation, *values),
                        )
                    text = self.recognize(data, generation, stop)
                if not self.valid(generation):
                    return
                kind, query = command(text, prefs)
                event(
                    "handsfree.command_result",
                    generation=generation,
                    recognized=kind != "unknown",
                    command=kind,
                )
                if kind != "cancel":
                    self.finished.clear()

                    def deliver(kind=kind, query=query, text=text):
                        if not self.valid(generation):
                            return
                        try:
                            if kind == "open":
                                self.launch(query, generation, heard=text)
                            elif kind != "unknown" and self.desktop_action:
                                self.desktop_action(kind, query, generation, heard=text)
                            else:
                                self.feedback(
                                    "Command not recognized. See Usage & Commands for supported phrases."
                                    if text
                                    else "No command heard. Try again after the beep.",
                                    heard=text,
                                )
                                self.complete(generation)
                        except Exception:
                            # Desktop integration failure cannot leave the listener
                            # stuck waiting for an action that was never presented.
                            self.complete(generation)

                    self.session.dispatch(deliver)
                    self.await_action(generation, stop, prefs)
                # Discard all audio during feedback and allow its sound to decay.
                if stop.wait(1):
                    return
        except Exception as exc:
            failure("handsfree.failed", exc, generation=generation)

            def fail():
                if self.valid(generation):
                    self.suspend()
                    self.failed = True
                    self.resume_at = time.monotonic() + 5
                    self.state = "unavailable"
                    self.problem = "Hands-free listening stopped. Retrying in five seconds; check the microphone and local model if this continues."
                    self.changed()

            self.session.dispatch(fail)
