# SPDX-License-Identifier: GPL-3.0-only
from array import array
import base64
from dataclasses import replace
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from dictadesk.core.voice_commands import (
    words,
    wake_matches,
    wake_match_kind,
    command,
    match_applications,
    diagnostic,
    choice_number,
)
from dictadesk.core.settings import Preferences, SettingsStore
from dictadesk.core.handsfree import HandsFree
from dictadesk.core.session import Session
from dictadesk.audio.handsfree import MemoryInput, utterance, PRE_ROLL_SAMPLES
from dictadesk.audio.worker import transcribe_pcm

VOICE = array("h", [4000] * 1600).tobytes()
QUIET = bytes(3200)


class App:
    def __init__(self, name, ident, visible=True):
        self.name, self.ident, self.visible = name, ident, visible

    def get_display_name(self):
        return self.name

    def get_name(self):
        return self.name

    def get_id(self):
        return self.ident

    def should_show(self):
        return self.visible


class ParsingTests(unittest.TestCase):
    def test_wake_matches_complete_words_only(self):
        for text in ("Hey, MINT!", "Well... hey mint.", "HEY—MINT", "Hey mint open Firefox"):
            self.assertTrue(wake_matches(text, "Hey Mint"), text)
        for text in ("they mint", "hey minty", "hey peppermint", "heymint", "hey then mint"):
            self.assertFalse(wake_matches(text, "Hey Mint"), text)
        self.assertEqual(words("HÉY—Ｍｉｎｔ"), ("héy", "mint"))
        self.assertFalse(wake_matches("hello", "..."))

    def test_isolated_wake_tolerance_is_bounded_and_configured(self):
        self.assertEqual(wake_match_kind("Clanker!", "Clanker"), "exact")
        for heard in ("Clank.", "Clanger."):
            self.assertEqual(wake_match_kind(heard, "Clanker"), "limited")
        self.assertTrue(wake_matches("Hey clank", "Hey Clanker"))
        self.assertTrue(wake_matches("Thund", "Thunder"))
        self.assertFalse(wake_matches("Clank", "Thunder"))
        for heard in (
            "Langer",
            "Planker",
            "Clender",
            "Clunker",
            "Clan",
            "Clanky",
            "clank er",
            "a clank",
            "there was a clanger",
            "please clank",
            "clank open Firefox",
        ):
            self.assertFalse(wake_matches(heard, "Clanker"), heard)
        self.assertFalse(wake_matches("they clank", "Hey Clanker"))
        self.assertFalse(wake_matches("Thund comput", "Thunder Computer"))
        self.assertFalse(wake_matches("123756", "123456"))
        # Wake tolerance must never change the deterministic command grammar.
        for text in ("Clank", "volum up", "locks screan", "suspent computer"):
            self.assertEqual(command(text)[0], "unknown")

    def test_defaults_validation_and_roundtrip(self):
        p = Preferences()
        self.assertFalse(p.hands_free)
        self.assertTrue(p.command_sound)
        self.assertEqual(p.wake_phrase, "Clanker")
        for phrase in ("", "   ", "!!!", "a " * 13, "x" * 121):
            with self.assertRaises(ValueError):
                replace(p, wake_phrase=phrase).validate()
        with tempfile.TemporaryDirectory() as folder:
            store = SettingsStore(Path(folder) / "prefs.json")
            changed = replace(p, hands_free=True, wake_phrase="Hey Mint", command_sound=False)
            store.save(changed)
            self.assertEqual(store.read(), changed)

    def test_command_scope_and_exact_application_resolution(self):
        apps = [
            App("Firefox", "firefox.desktop"),
            App("Firefox Developer", "dev.desktop"),
            App("Calibre", "calibre.desktop"),
            App("Hidden", "hidden.desktop", False),
        ]
        self.assertEqual(command("Launch Calibre!"), ("open", ("calibre",)))
        self.assertEqual(command("Cancel."), ("cancel", ()))
        for text in ("remove files", "open", "please launch firefox"):
            self.assertEqual(command(text)[0], "unknown")
        self.assertEqual(match_applications(("firefox",), apps), [apps[0]])
        apps[0].name = "Firefox Browser"
        # The stable desktop ID resolves Firefox even when its menu label changes.
        self.assertEqual(match_applications(("firefox",), apps), [apps[0]])
        for query in (("fire",), ("hidden",), ("firefox", "rm", "rf")):
            self.assertFalse(match_applications(query, apps))

    def test_numbered_choice_parser(self):
        for text, result in (
            ("One!", 1),
            ("number 2", 2),
            ("option three", 3),
            ("four", 4),
            ("5", 5),
            ("other", "more"),
            ("next page", "more"),
            ("Cancel.", "cancel"),
        ):
            self.assertEqual(choice_number(text), result)
        for text in ("open one", "one two", "six", "someone", ""):
            self.assertIsNone(choice_number(text))

    def test_terminal_restricted_to_other_desktops_still_beats_server_client(self):
        class DesktopApp(App):
            def get_nodisplay(self):
                return False

            def get_is_hidden(self):
                return False

            def get_executable(self):
                return "gnome-terminal"

        terminal = DesktopApp("Terminal", "org.gnome.Terminal.desktop", visible=False)
        server = App("Terminal Server Client", "tsclient.desktop")
        helper = DesktopApp("Terminal", "terminal-helper.desktop")
        helper.get_nodisplay = lambda: True
        deleted = DesktopApp("Terminal", "deleted.desktop")
        deleted.get_is_hidden = lambda: True
        apps = [server, helper, deleted, terminal]
        self.assertEqual(match_applications(("terminal",), apps), [terminal])
        self.assertEqual(match_applications(("gnome", "terminal"), apps), [terminal])
        self.assertEqual(match_applications(("terminal", "server", "client"), apps), [server])

    def test_spelling_suggestions_do_not_override_exact_names(self):
        calibre = App("Calibre", "calibre.desktop")
        caliber = App("Caliber", "caliber.desktop")
        self.assertEqual(match_applications(("caliber",), [calibre]), [calibre])
        self.assertEqual(match_applications(("caliber",), [calibre, caliber]), [caliber])
        self.assertFalse(match_applications(("zzzz",), [calibre, caliber]))
        self.assertFalse(match_applications((), [calibre, caliber]))


class AudioTests(unittest.TestCase):
    def test_bounded_utterance_and_silence_gating(self):
        def run(blocks, **kwargs):
            sequence = iter(blocks)
            source = SimpleNamespace(read=lambda *_: next(sequence, None))
            return utterance(
                source, threading.Event(), threshold=0.015, deadline=float("inf"), **kwargs
            )

        self.assertEqual(run([QUIET] * 100), b"")
        self.assertEqual(run([VOICE] + [QUIET] * 5), b"")
        data = run([VOICE] * 100)
        self.assertEqual(len(data), 8 * 16000 * 2)
        self.assertEqual(
            run([VOICE] * 5 + [QUIET] * 3 + [VOICE] * 2 + [QUIET] * 5, require_quiet=True),
            QUIET * 2 + VOICE * 2 + QUIET * 5,
        )

    def test_preroll_preserves_weak_onset_and_does_not_relax_speech_gating(self):
        weak = array("h", [300] * 1600).tobytes()
        sequence = iter([QUIET] * 3 + [weak] * 2 + [VOICE] * 2 + [QUIET] * 5)
        source = SimpleNamespace(read=lambda *_: next(sequence, None))
        data = utterance(
            source,
            threading.Event(),
            threshold=0.015,
            deadline=float("inf"),
            require_quiet=True,
            max_seconds=6,
        )
        self.assertEqual(data, weak * 2 + VOICE * 2 + QUIET * 5)
        # Quiet audio and a single isolated click still cannot trigger inference.
        for blocks in ([weak] * 20, [QUIET] * 3 + [VOICE] + [QUIET] * 5):
            sequence = iter(blocks)
            self.assertEqual(
                utterance(
                    source,
                    threading.Event(),
                    threshold=0.015,
                    deadline=float("inf"),
                    require_quiet=True,
                ),
                b"",
            )

    def test_preroll_is_fresh_for_every_capture_and_excluded_from_voiced_count(self):
        for block in (VOICE, array("h", [7000] * 1600).tobytes()):
            sequence = iter([block] * 2 + [QUIET] * 5)
            source = SimpleNamespace(read=lambda *_: next(sequence, None))
            data = utterance(source, threading.Event(), threshold=0.015, deadline=float("inf"))
            self.assertEqual(data, bytes(PRE_ROLL_SAMPLES * 2) + block * 2 + QUIET * 5)

    def test_memory_protocol_never_uses_cloud_or_files(self):
        model = Mock()
        model.recognize.return_value = "Hey Mint"
        with (
            patch("builtins.open", side_effect=AssertionError("no recordings")),
            patch("urllib.request.urlopen", side_effect=AssertionError("no uploads")),
        ):
            self.assertEqual(
                transcribe_pcm(model, base64.b64encode(VOICE).decode()), "Hey Mint"
            )
        audio = model.recognize.call_args.args[0]
        self.assertEqual(len(audio), 1600)
        self.assertEqual(model.recognize.call_args.kwargs, {"sample_rate": 16000})
        for payload in ("?", "", base64.b64encode(b"x").decode(), "A" * 400000):
            with self.assertRaises(ValueError):
                transcribe_pcm(model, payload)

    def test_microphone_queue_is_bounded_and_start_failure_closes_stream(self):
        streams = []

        class Stream:
            def __init__(self, **kwargs):
                self.callback = kwargs["callback"]
                streams.append(self)

            def start(self):
                for _ in range(100):
                    self.callback(VOICE, 1600, None, None)

            def abort(self):
                self.aborted = True

            def close(self):
                self.closed = True

        with patch("sounddevice.RawInputStream", Stream):
            with MemoryInput(Preferences()) as source:
                self.assertLessEqual(source.blocks.qsize(), 5)
            self.assertTrue(streams[0].closed and streams[0].aborted)
        stream = Mock()
        stream.start.side_effect = RuntimeError("unavailable")
        with (
            patch("sounddevice.RawInputStream", return_value=stream),
            self.assertRaises(RuntimeError),
        ):
            with MemoryInput(Preferences()):
                pass
        stream.close.assert_called_once()


class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.session = SimpleNamespace(
            preferences=Preferences(hands_free=True),
            busy=False,
            closing=False,
            microphone_problem="",
            problem=None,
            store=SimpleNamespace(problem=""),
            recognizer=Mock(loaded=True),
            dispatch=lambda fn: fn(),
            load_available_model=Mock(),
        )
        self.changed, self.launch, self.feedback = Mock(), Mock(), Mock()
        self.hf = HandsFree(self.session, self.changed, self.launch, self.feedback)
        self.hf.resume_at = 0

    def prepare(self, replies):
        self.session.recognizer.request.side_effect = [{"text": reply} for reply in replies]
        active = [0]

        class Source:
            def __init__(self, prefs):
                pass

            def __enter__(self):
                active[0] += 1
                return self

            def __exit__(self, *_):
                active[0] -= 1

            def discard(self):
                pass

        self.hf.source = Source
        self.hf.beep = lambda stop: self.assertEqual(active[0], 0)
        self.hf.stop.wait = lambda timeout: self.hf.stop.is_set()
        return active

    def test_wake_and_next_utterance_are_separate_and_local(self):
        active = self.prepare(["Hey Clanker open Firefox", "open Calibre"])

        def launch(query, generation, *, heard):
            self.assertEqual(query, ("calibre",))
            self.assertEqual(heard, "open Calibre")
            self.assertEqual(active[0], 0)
            self.hf.stop.set()

        self.launch.side_effect = launch
        with patch("dictadesk.core.handsfree.utterance", return_value=VOICE):
            self.hf.run(0, self.hf.stop, self.session.preferences)
        self.launch.assert_called_once()
        self.assertEqual(self.hf.recognitions, 2)
        self.assertTrue(
            all(
                c.args[0]["op"] == "transcribe_pcm"
                for c in self.session.recognizer.request.call_args_list
            )
        )

    def test_desktop_actions_use_the_same_local_wake_flow_with_closed_capture(self):
        for text, kind, arguments in (
            ("lock screen", "lock", ()),
            ("screensaver", "screensaver", ()),
            ("volume up 15%", "volume", ("up", 15)),
            ("volume down fifty percent", "volume", ("down", 50)),
            ("volume mute", "volume", ("mute",)),
        ):
            with self.subTest(text=text):
                self.setUp()
                active = self.prepare(["Clanker", text])
                action = Mock()

                def execute(actual_kind, actual_arguments, generation, *, heard):
                    self.assertEqual((actual_kind, actual_arguments), (kind, arguments))
                    self.assertEqual(active[0], 0)
                    self.assertEqual(heard, text)
                    self.hf.complete(generation)

                action.side_effect = execute
                self.hf.desktop_action = action
                with patch("dictadesk.core.handsfree.utterance", return_value=VOICE):
                    self.hf.run(0, self.hf.stop, self.session.preferences)
                action.assert_called_once()
                self.launch.assert_not_called()

    def test_wake_captures_origin_before_beep_and_retires_action_once(self):
        target = object()
        active = self.prepare(["My custom phrase", "maximize window"])
        self.session.preferences.wake_phrase = "My custom phrase"
        self.hf.capture_destination = Mock(return_value=target)
        self.hf.beep = lambda stop: self.assertIs(self.hf.activation_target, target)
        actions = []

        def execute(kind, arguments, generation, **kwargs):
            self.assertEqual(active[0], 0)
            self.assertIs(self.hf.activation_target, target)
            actions.append((kind, arguments))
            self.hf.complete(generation)

        self.hf.desktop_action = execute
        with patch("dictadesk.core.handsfree.utterance", return_value=VOICE):
            self.hf.run(0, self.hf.stop, self.session.preferences)
        self.assertEqual(actions, [("window", ("maximize",))])
        self.hf.capture_destination.assert_called_once()
        self.assertFalse(self.hf.valid(0))

    def test_timed_pause_releases_capture_preserves_dictation_and_resumes(self):
        with patch("dictadesk.core.handsfree.time.monotonic", return_value=100):
            self.hf.signature = None
            self.hf.pause(30)
            self.assertTrue(self.hf.paused)
            self.assertEqual(self.hf.pause_until, 1900)
            self.assertTrue(self.hf.stop.is_set())
            self.assertFalse(self.hf.valid(self.hf.generation))
            with patch("dictadesk.core.handsfree.threading.Thread") as thread:
                self.hf.tick()
                thread.assert_not_called()
                self.assertEqual(self.hf.state, "paused")
        with (
            patch("dictadesk.core.handsfree.time.monotonic", return_value=1901),
            patch("dictadesk.core.handsfree.threading.Thread") as thread,
        ):
            self.hf.tick()
            thread.return_value.start.assert_called_once()
            self.assertFalse(self.hf.paused)
        self.hf.pause(30)
        self.hf.resume()
        self.assertFalse(self.hf.paused)
        self.assertTrue(self.hf.enabled)

    def test_unknown_command_reports_only_post_wake_words(self):
        self.prepare(["private background Hey Clanker", "please open the thing"])
        with patch("dictadesk.core.handsfree.utterance", return_value=VOICE):
            self.hf.run(0, self.hf.stop, self.session.preferences)
        self.launch.assert_not_called()
        self.feedback.assert_called_once_with(
            "Command not recognized. See Usage & Commands for supported phrases.",
            heard="please open the thing",
        )

    def test_diagnostic_bounds_and_normalizes_display_without_altering_query(self):
        text = diagnostic("Open\n Firefox\x00!", ("firefox",))
        self.assertEqual(text, "Heard: “Open Firefox !”\nLooking for application: “firefox”")
        self.assertLess(len(diagnostic("a" * 10000, ("b" * 10000,))), 400)
        self.assertEqual(diagnostic(""), "Heard: No command recognized.")

    def test_spoken_choice_uses_same_local_worker_and_releases_capture(self):
        active = self.prepare(["Hey Clanker", "open Calibre", "two"])
        selected = []

        def choose(number, heard):
            self.assertEqual(active[0], 0)
            selected.append((number, heard))
            self.hf.complete(0)

        self.launch.side_effect = lambda query, generation, **kw: self.hf.offer_choices(
            generation, choose
        )
        with patch("dictadesk.core.handsfree.utterance", return_value=VOICE):
            self.hf.run(0, self.hf.stop, self.session.preferences)
        self.assertEqual(selected, [(2, "two")])
        self.assertEqual(self.hf.recognitions, 3)
        self.assertTrue(
            all(
                c.args[0]["op"] == "transcribe_pcm"
                for c in self.session.recognizer.request.call_args_list
            )
        )

    def test_old_page_recognition_cannot_choose_from_new_page(self):
        self.prepare([])
        old = Mock()
        selected = []

        def choose(number, heard):
            selected.append(number)
            self.hf.complete(0)

        self.hf.offer_choices(0, old)
        old_page = self.hf.choice
        calls = []

        def recognize(*a, **kw):
            calls.append(True)
            if len(calls) == 1:
                self.hf.offer_choices(0, choose)
                return {"text": "one"}
            return {"text": "two"}

        self.session.recognizer.request.side_effect = recognize
        with patch("dictadesk.core.handsfree.utterance", return_value=VOICE):
            self.hf.await_action(0, self.hf.stop, self.session.preferences)
        old.assert_not_called()
        self.assertTrue(old_page[2].is_set())
        self.assertEqual(selected, [2])

    def test_queued_choice_cannot_run_after_dictation_or_disable(self):
        for change in ("dictation", "disable", "remote"):
            self.setUp()
            self.prepare(["one"])
            choose = Mock()
            self.hf.offer_choices(0, choose)
            queued = []

            def dispatch(fn):
                queued.append(fn)
                if change == "dictation":
                    self.session.busy = True
                elif change == "disable":
                    self.hf.suspend()
                else:
                    self.session.preferences.backend = "openai"

            self.session.dispatch = dispatch
            with patch("dictadesk.core.handsfree.utterance", return_value=VOICE):
                self.hf.await_action(0, self.hf.stop, self.session.preferences)
            self.assertEqual(len(queued), 1)
            queued[0]()
            choose.assert_not_called()

    def test_choice_timeout_and_cancel(self):
        for reply in ("cancel", "timeout"):
            self.setUp()
            self.prepare([reply])
            received = []

            def choose(number, heard):
                received.append(number)
                self.hf.complete(0)

            self.hf.offer_choices(0, choose)
            with (
                patch(
                    "dictadesk.core.handsfree.utterance",
                    return_value=VOICE if reply == "cancel" else b"",
                ),
                patch(
                    "dictadesk.core.handsfree.time.monotonic", side_effect=[0, 0, 0, 21, 22, 23]
                ),
            ):
                self.hf.await_action(0, self.hf.stop, self.session.preferences)
            self.assertEqual(received, [reply])

    def test_late_recognition_cannot_launch_after_disable_or_dictation(self):
        for change in ("disable", "dictation", "remote", "cancel", "lock"):
            with self.subTest(change=change):
                self.setUp()
                self.prepare(["Hey Clanker"])

                def recognition(payload, *_args, **_kwargs):
                    if self.hf.recognitions == 1:
                        return {"text": "Hey Clanker"}
                    if change == "disable":
                        self.session.preferences.hands_free = False
                    elif change == "lock":
                        self.session.desktop_blocked = True
                    elif change == "dictation":
                        self.session.busy = True
                    elif change == "remote":
                        self.session.preferences.backend = "openai"
                    else:
                        self.hf.suspend()
                    return {"text": "open Firefox"}

                self.session.recognizer.request.side_effect = recognition
                with patch("dictadesk.core.handsfree.utterance", return_value=VOICE):
                    self.hf.run(0, self.hf.stop, self.session.preferences)
                self.launch.assert_not_called()

    def test_queued_action_is_invalidated_before_delivery(self):
        self.prepare(["Hey Clanker", "open Firefox"])
        queued = []

        def dispatch(fn):
            if fn.__name__ == "deliver":
                queued.append(fn)
                self.hf.suspend()
            else:
                fn()

        self.session.dispatch = dispatch
        with patch("dictadesk.core.handsfree.utterance", return_value=VOICE):
            self.hf.run(0, self.hf.stop, self.session.preferences)
        self.assertEqual(len(queued), 1)
        queued[0]()
        self.launch.assert_not_called()

    def test_cancel_and_timeout_return_to_wake_without_reusing_audio(self):
        for text in ("cancel", ""):
            with self.subTest(text=text):
                self.setUp()
                self.prepare(["Hey Clanker", text])
                calls = []

                def audio(*_, **kwargs):
                    calls.append(kwargs)
                    if len(calls) == 3:
                        self.hf.stop.set()
                    return VOICE

                with patch("dictadesk.core.handsfree.utterance", side_effect=audio):
                    self.hf.run(0, self.hf.stop, self.session.preferences)
                self.launch.assert_not_called()
                if text:
                    self.feedback.assert_not_called()
                    self.assertEqual(self.hf.state, "wake")
                else:
                    self.feedback.assert_called_once_with(
                        "No command heard. Try again after the beep.", heard=""
                    )
                self.assertLessEqual(calls[1]["deadline"] - time.monotonic(), 8)
                self.assertEqual(self.hf.recognitions, 2)

    def test_remote_disabled_and_unavailable_models_do_not_capture(self):
        self.hf.source = Mock(side_effect=AssertionError("must not capture"))
        for backend, enabled in (("openai", True), ("local", False)):
            self.session.preferences = Preferences(backend=backend, hands_free=enabled)
            self.hf.tick()
            self.hf.resume_at = 0
            self.hf.tick()
            self.hf.source.assert_not_called()
            self.session.recognizer.request.assert_not_called()
        self.session.preferences = Preferences(hands_free=True)
        self.session.recognizer.loaded = False
        self.hf.tick()
        self.hf.resume_at = 0
        self.hf.tick()
        self.assertEqual(self.hf.state, "unavailable")
        self.hf.source.assert_not_called()

    def test_lock_promptly_releases_wake_and_choice_capture(self):
        for choosing in (False, True):
            self.setUp()
            entered, closed = threading.Event(), threading.Event()

            class Source:
                def __init__(self, prefs):
                    pass

                def __enter__(self):
                    entered.set()
                    return self

                def __exit__(self, *_):
                    closed.set()

                def read(self, stop, deadline):
                    stop.wait(1)
                    return None

            self.hf.source = Source
            if choosing:
                self.hf.offer_choices(0, Mock())
            target = self.hf.await_action if choosing else self.hf.run
            thread = threading.Thread(
                target=target, args=(0, self.hf.stop, self.session.preferences)
            )
            thread.start()
            self.assertTrue(entered.wait(1))
            self.session.desktop_blocked = True
            self.hf.suspend()
            thread.join(1)
            self.assertFalse(thread.is_alive())
            self.assertTrue(closed.is_set())
            self.session.recognizer.request.assert_not_called()

    def test_lock_pauses_capture_and_unlock_respects_disabled_preference(self):
        self.session.desktop_blocked = True
        self.session.desktop_reason = "Screen locked"
        self.hf.source = Mock(side_effect=AssertionError("must not capture"))
        self.hf.tick()
        self.assertEqual(self.hf.state, "paused")
        self.assertFalse(self.hf.valid(self.hf.generation))
        self.hf.source.assert_not_called()
        self.session.desktop_blocked = False
        self.session.preferences.hands_free = False
        self.hf.tick()
        self.hf.source.assert_not_called()
        self.session.preferences.hands_free = True
        self.hf.tick()
        self.hf.resume_at = 0
        with patch("dictadesk.core.handsfree.threading.Thread") as worker:
            self.hf.tick()
            worker.return_value.start.assert_called_once()

    def test_completion_invalidates_old_chooser_even_after_next_activation(self):
        generation = self.hf.generation
        self.assertTrue(self.hf.valid(generation))
        self.hf.complete(generation)
        self.hf.stop.clear()
        self.assertFalse(self.hf.valid(generation))

    def test_microphone_failure_does_not_restart_in_a_loop(self):
        self.hf.source = Mock(side_effect=RuntimeError("unavailable"))
        self.hf.run(0, self.hf.stop, self.session.preferences)
        self.assertTrue(self.hf.failed)
        self.assertEqual(self.hf.state, "unavailable")

    def test_previous_processing_error_does_not_permanently_block_model_reload(self):
        with patch("dictadesk.core.handsfree.threading.Thread"):
            self.hf.tick()
        self.hf.thread = None
        self.hf.suspend()
        self.hf.resume_at = 0
        self.session.recognizer.loaded = False
        self.session.problem = (1, "recording", "previous error")
        self.hf.tick()
        self.session.load_available_model.assert_called_once()
        self.hf.tick()
        self.session.load_available_model.assert_called_once()
        self.hf.model_retry_at = 0
        self.hf.tick()
        self.assertEqual(self.session.load_available_model.call_count, 2)

    def test_microphone_failure_retries_after_backoff_without_manual_toggle(self):
        self.hf.signature = (
            self.session.preferences.backend,
            self.session.preferences.hands_free,
            self.session.preferences.wake_phrase,
            self.session.preferences.command_sound,
            self.session.preferences.local_model,
            self.session.preferences.quantization,
            self.session.preferences.device,
            self.session.preferences.microphone,
            self.session.preferences.host_api,
            self.session.preferences.silence_threshold,
        )
        self.hf.source = Mock(side_effect=RuntimeError("temporary failure"))
        self.hf.run(0, self.hf.stop, self.session.preferences)
        with patch("dictadesk.core.handsfree.threading.Thread") as thread:
            self.hf.tick()
            thread.assert_not_called()
            self.hf.resume_at = 0
            self.hf.tick()
            thread.return_value.start.assert_called_once()
        self.assertFalse(self.hf.failed)

    def test_manual_recording_failure_returns_microphone_to_wake_listener(self):
        self.hf.signature = None
        self.session.busy = True
        self.hf.tick()
        self.assertFalse(self.hf.valid(self.hf.generation))
        self.session.busy = False
        self.hf.resume_at = 0
        with patch("dictadesk.core.handsfree.threading.Thread") as thread:
            self.hf.tick()
            thread.return_value.start.assert_called_once()
        self.assertTrue(self.hf.valid(self.hf.generation))

    def test_disable_during_inference_has_already_released_microphone(self):
        active = self.prepare([])
        entered, release = threading.Event(), threading.Event()

        def request(*_, **__):
            entered.set()
            release.wait(2)
            return {"text": "Hey Clanker open Firefox"}

        self.session.recognizer.request.side_effect = request
        with patch("dictadesk.core.handsfree.utterance", return_value=VOICE):
            thread = threading.Thread(
                target=self.hf.run, args=(0, self.hf.stop, self.session.preferences)
            )
            thread.start()
            self.assertTrue(entered.wait(1))
            self.hf.suspend()
            self.assertEqual(active[0], 0)
            release.set()
            thread.join(2)
        self.launch.assert_not_called()
        self.session.recognizer.close.assert_not_called()

    def test_dictation_waits_for_capture_and_inference_to_release(self):
        with tempfile.TemporaryDirectory() as folder:
            session = Session(
                lambda fn: fn(),
                lambda: None,
                lambda *_: None,
                store=SettingsStore(Path(folder) / "prefs.json"),
                recognizer=Mock(loaded=True),
            )
            hf = HandsFree(session, lambda: None, lambda *_: None, lambda *_: None)
            session.handsfree = hf
            release = threading.Event()
            hf.thread = threading.Thread(target=lambda: release.wait(2))
            hf.thread.start()
            task = Mock(return_value=None)
            ticket = session._begin("starting")
            self.assertTrue(hf.stop.is_set())
            session._run(ticket, lambda *_: task(), lambda _: None)
            time.sleep(0.05)
            task.assert_not_called()
            release.set()
            session.thread.join(2)
            task.assert_called_once()
