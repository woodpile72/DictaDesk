# SPDX-License-Identifier: GPL-3.0-only
from array import array
from pathlib import Path
import queue
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import wave
from cffi import FFI

from dictadesk.audio.continuous import ContinuousCapture, Phrases
from dictadesk.core.session import Session
from dictadesk.core.settings import Preferences, SettingsStore
from dictadesk.core.transcript import continuation

VOICE = array("h", [5000] * 1600).tobytes()
SILENCE = bytes(3200)


class CaptureBufferTests(unittest.TestCase):
    def capture_buffers(self, blocks, silence_stop_seconds=0):
        capture = ContinuousCapture(Preferences(), Path("unused"))
        capture.silence_stop_seconds = silence_stop_seconds

        class Input:
            def __init__(self, **kwargs):
                self.callback = kwargs["callback"]

            def __enter__(self):
                for block in blocks:
                    self.callback(block, 1600, None, None)
                    if capture.stop.is_set():
                        break
                capture.stop.set()
                return self

            def __exit__(self, *_):
                pass

        with (
            patch("sounddevice.RawInputStream", Input),
            patch("dictadesk.audio.continuous.resolve", return_value=None),
        ):
            capture.run(lambda: None)
        self.assertTrue(capture.done.is_set())
        return capture

    def test_silence_stop_is_twenty_seconds_from_last_speech_not_phrase_end(self):
        for seconds, stopped in ((19.9, False), (20, True)):
            blocks = [VOICE] * 2 + [SILENCE * 10] * 19 + [SILENCE * round((seconds - 19) * 10)]
            capture = self.capture_buffers(blocks, silence_stop_seconds=20)
            self.assertEqual(capture.silence_stopped, stopped)
            self.assertFalse(capture.timed_out)
            self.assertEqual(capture.problem, "")
            updates = []
            while capture.has_updates():
                updates.append(capture.next_update())
            self.assertTrue(updates[-1].final)
            self.assertTrue(updates[-1].audio.startswith(VOICE * 2))

    def test_custom_silence_stop_threshold_and_off(self):
        for seconds, stopped in ((2.9, False), (3, True)):
            capture = self.capture_buffers(
                [VOICE] * 2 + [SILENCE] * round(seconds * 10), silence_stop_seconds=3
            )
            self.assertEqual(capture.silence_stopped, stopped)
        capture = self.capture_buffers(
            [VOICE] * 2 + [SILENCE * 10] * 30, silence_stop_seconds=0
        )
        self.assertFalse(capture.silence_stopped)

    def test_resumed_speech_resets_auto_stop_and_continuous_mode_stays_open(self):
        capture = self.capture_buffers(
            [VOICE] * 2 + [SILENCE * 10] * 19 + [VOICE] * 2 + [SILENCE * 10] * 19,
            silence_stop_seconds=20,
        )
        self.assertFalse(capture.silence_stopped)
        capture = self.capture_buffers([SILENCE * 10] * 25)
        self.assertFalse(capture.silence_stopped)

    def test_initial_silence_and_noise_clicks_also_stop(self):
        noisy = array("h", [10000] + [120, -120] * 799 + [120]).tobytes()
        capture = self.capture_buffers([noisy * 10] * 20, silence_stop_seconds=20)
        self.assertTrue(capture.silence_stopped)
        self.assertFalse(capture.has_updates())

    def test_portaudio_cffi_buffer_is_copied_before_reuse(self):
        ffi = FFI()
        storage = ffi.new("char[]", VOICE)
        buffer = ffi.buffer(storage, len(VOICE))

        def blocks():
            yield buffer
            yield buffer
            # PortAudio can overwrite its buffer immediately after callback return.
            buffer[:] = SILENCE

        capture = self.capture_buffers(blocks())
        self.assertEqual(capture.problem, "")
        self.assertAlmostEqual(capture.peak, 5000 / 32768)
        self.assertEqual(capture.phrases.get_nowait().audio, VOICE * 2)
        self.assertTrue(capture.phrases.empty())

    def test_callback_failure_stops_capture_and_reports_problem(self):
        capture = self.capture_buffers([VOICE, VOICE, b"\x01"])
        self.assertTrue(capture.stop.is_set())
        self.assertIn("could not be read", capture.problem)
        self.assertEqual(capture.phrases.get_nowait().audio, VOICE * 2)

    def test_thinking_pause_preserves_audio_until_sentence_pause(self):
        capture = self.capture_buffers(
            [VOICE] * 2 + [SILENCE] * 10 + [VOICE] * 2 + [SILENCE] * 40
        )
        updates = []
        while capture.has_updates():
            updates.append(capture.next_update())
        self.assertEqual([u.final for u in updates], [False, False, True])
        self.assertEqual([u.segment for u in updates], [0, 0, 0])
        self.assertTrue(updates[0].paused)
        self.assertEqual(updates[0].audio, VOICE * 2 + SILENCE * 10)
        self.assertEqual(updates[-1].audio, VOICE * 2 + SILENCE * 10 + VOICE * 2 + SILENCE * 40)
        self.assertTrue(updates[-1].sentence_end)

    def test_stop_flushes_before_sentence_timeout(self):
        capture = self.capture_buffers([VOICE] * 2 + [SILENCE] * 10)
        first, last = capture.next_update(), capture.next_update()
        self.assertTrue(first.paused)
        self.assertFalse(first.final)
        self.assertTrue(last.final)
        self.assertTrue(last.sentence_end)
        self.assertEqual(first.audio, last.audio)

    def test_capacity_boundary_still_finalizes_after_silence_or_stop(self):
        for quiet_blocks in (10, 45):
            with patch(
                "dictadesk.audio.continuous.Phrases",
                side_effect=lambda pause, threshold, **kw: Phrases(
                    pause, threshold, max_seconds=0.3, defer_limit_during_pause=True
                ),
            ):
                capture = self.capture_buffers([VOICE] * 3 + [SILENCE] * quiet_blocks)
            first, last = capture.next_update(), capture.next_update()
            self.assertEqual(first.segment, last.segment)
            self.assertFalse(first.sentence_end)
            self.assertTrue(last.sentence_end)
            self.assertEqual(first.audio, last.audio)
            self.assertFalse(capture.has_updates())


class PhraseTests(unittest.TestCase):
    def test_pause_commits_once_and_final_partial_is_retained(self):
        p = Phrases(0.3, 0.01)
        self.assertIsNone(p.add(VOICE))
        self.assertIsNone(p.add(VOICE))
        self.assertIsNone(p.add(SILENCE))
        self.assertIsNone(p.add(SILENCE))
        self.assertEqual(p.add(SILENCE), VOICE * 2 + SILENCE * 3)
        self.assertIsNone(p.add(SILENCE))
        p.add(VOICE)
        p.add(VOICE)
        self.assertEqual(p.finish(), VOICE * 2)
        self.assertIsNone(p.finish())

    def test_silence_does_not_accumulate_or_request_recognition(self):
        p = Phrases(0.8, 0.01)
        for _ in range(10000):
            self.assertIsNone(p.add(SILENCE))
        self.assertEqual(len(p.audio), 0)
        self.assertIsNone(p.finish())

    def test_pause_with_noise_spikes_commits_before_stop(self):
        p = Phrases(0.3, 0.015)
        noisy_pause = array("h", [10000] + [120, -120] * 799 + [120]).tobytes()
        self.assertIsNone(p.add(VOICE))
        self.assertIsNone(p.add(VOICE))
        self.assertIsNone(p.add(noisy_pause))
        self.assertIsNone(p.add(noisy_pause))
        self.assertEqual(p.add(noisy_pause), VOICE * 2 + noisy_pause * 3)
        for _ in range(20):
            self.assertIsNone(p.add(noisy_pause))
        self.assertIsNone(p.finish())

    def test_unbroken_speech_emits_by_eight_seconds_without_stop(self):
        p = Phrases(0.8, 0.015)
        chunks = [p.add(VOICE) for _ in range(80)]
        self.assertEqual([chunk for chunk in chunks if chunk], [VOICE * 80])
        self.assertIsNone(p.finish())

    def test_long_speech_has_bounded_phrases(self):
        p = Phrases(0.8, 0.01, max_seconds=1)
        emitted = [p.add(VOICE) for _ in range(100)]
        chunks = [x for x in emitted if x]
        self.assertEqual(len(chunks), 10)
        self.assertTrue(all(len(x) == 32000 for x in chunks))

    def test_full_phrase_queue_cancel_does_not_deadlock(self):
        c = ContinuousCapture(Preferences(), Path("unused"))
        for _ in range(8):
            c.phrases.put(VOICE)
        thread = threading.Thread(target=c.emit, args=(VOICE,))
        thread.start()
        c.cancel.set()
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(c.phrases.qsize(), 8)

    def test_spacing_across_committed_phrases(self):
        for before, after, expected in (
            ("Hello", ".", "."),
            ("Hello.", "next", "Next"),
            ("Hello", "world", " world"),
            ("Hello", "\n\n", "\n\n"),
            ("Note (", "text)", "text)"),
            ("Note", "(text)", " (text)"),
        ):
            with self.subTest(before=before, after=after):
                # Sentence continuation needs a space after punctuation.
                if before == "Hello." and after == "next":
                    expected = " Next"
                self.assertEqual(continuation(before, after), expected)


class FakeCapture(ContinuousCapture):
    def run(self, started):
        started()
        for _ in range(3):
            self.phrases.put(VOICE * 2)
        self.done.set()


class FakeRecognizer:
    loaded = True

    def __init__(self):
        self.replies = iter(["Hello comma world", "period", "next dot dot dot"])
        self.requests = []

    def request(self, payload, cancel, **kwargs):
        with wave.open(payload["path"]) as audio:
            assert audio.getnframes() == 3200
        self.requests.append(payload["op"])
        return {"text": next(self.replies)}

    def close(self):
        self.loaded = False


class ContinuousSessionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        env = patch.dict("os.environ", {"XDG_CACHE_HOME": str(self.root)})
        env.start()
        self.addCleanup(env.stop)
        capture = patch("dictadesk.audio.continuous.ContinuousCapture", FakeCapture)
        capture.start()
        self.addCleanup(capture.stop)
        self.callbacks = queue.Queue()
        self.full_deliveries = []
        self.worker = FakeRecognizer()
        self.session = Session(
            self.callbacks.put,
            lambda: None,
            lambda *args: self.full_deliveries.append(args),
            SettingsStore(self.root / "settings"),
            self.worker,
        )
        self.session.microphone_problem = ""
        self.session.preferences.spoken_punctuation = True
        self.session.preferences.continuous_live = False
        self.sent = []

        def deliver(value, target, done):
            self.sent.append(value)
            done(True)

        self.session.stream_delivered = deliver
        self.events = []
        self.session.recording_event = lambda kind, body: self.events.append(kind)

    def drain(self):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                self.callbacks.get(timeout=0.05)()
            except queue.Empty:
                pass
            if not self.session.thread.is_alive() and self.callbacks.empty():
                break
        self.assertFalse(self.session.thread.is_alive(), "Continuous worker did not finish")
        self.assertEqual(list(self.root.rglob("*.wav")), [])

    def test_commits_in_order_without_pasting_whole_transcript_again(self):
        self.session.record_continuous(target=object())
        self.drain()
        self.assertEqual(self.sent, ["Hello, world", ".", " Next…"])
        self.assertEqual(self.session.result.text, "Hello, world. Next…")
        self.assertEqual(self.session.pending_text, "")
        self.assertEqual(self.full_deliveries, [])
        self.assertFalse(self.session.continuous)
        self.assertEqual(self.events, ["started", "stopped", "completed"])
        self.assertEqual(self.session.notice, "")

    def test_dictation_does_not_recognize_or_execute_system_commands(self):
        self.worker.replies = iter(["volume max", "lock screen", "suspend computer"])
        with patch("dictadesk.core.commands.recognize", side_effect=AssertionError("Commands must not run in dictation")):
            self.session.record_continuous(target=object(), pause_mode=True)
            self.drain()
        self.assertIn("lock screen", self.session.result.text.lower())
        self.assertEqual(self.session.last_dictation, self.session.result.text)

    def test_auto_stop_only_for_pause_shortcut_mode(self):
        self.session.preferences.silence_stop_seconds = 7
        for pause_mode, expected in ((True, 7), (False, 0)):
            self.worker.replies = iter(["Hello", "world", "again"])
            with patch("dictadesk.audio.continuous.IncrementalCapture", FakeCapture):
                self.session.record_continuous(
                    target=object(), pause_mode=pause_mode
                )
                self.assertEqual(self.session.capture.silence_stop_seconds, expected)
                self.drain()

    def test_auto_stop_can_be_disabled_for_pause_shortcut_mode(self):
        self.session.preferences.silence_stop_seconds = 0
        self.session.record_continuous(target=object(), pause_mode=True)
        self.assertEqual(self.session.capture.silence_stop_seconds, 0)
        self.drain()
        self.assertEqual(self.events, ["started", "stopped", "completed"])

    def test_auto_stop_finishes_and_notifies_without_duplicate_insertion(self):
        closed = []

        class Input:
            def __init__(self, **kwargs):
                self.callback = kwargs["callback"]

            def __enter__(self):
                for block in [VOICE] * 2 + [SILENCE * 10] * 20:
                    self.callback(block, len(block) // 2, None, None)
                return self

            def __exit__(self, *_):
                closed.append(True)

        self.worker.request = lambda *_args, **_kwargs: {"text": "Hello there."}
        with (
            patch("dictadesk.audio.continuous.ContinuousCapture", ContinuousCapture),
            patch("sounddevice.RawInputStream", Input),
            patch("dictadesk.audio.continuous.resolve", return_value=None),
        ):
            self.session.record_continuous(target=object(), pause_mode=True)
            self.drain()
        self.assertEqual(closed, [True])
        self.assertEqual(self.sent, ["Hello", " there."])
        self.assertEqual(self.session.result.text, "Hello there.")
        self.assertEqual(self.session.pending_text, "")
        self.assertFalse(self.session.cancelled.is_set())
        self.assertEqual(self.events, ["started", "stopped", "completed"])

    def test_silent_recording_preserves_previous_result_without_copy(self):
        self.worker.replies = iter(["", "", ""])
        self.session.result.replace("Previous result", [])
        with patch("dictadesk.audio.continuous.IncrementalCapture", FakeCapture):
            self.session.record_continuous()
            self.drain()
        self.assertEqual(self.full_deliveries, [])
        self.assertEqual(self.session.result.text, "Previous result")
        self.assertIn("No speech", self.session.notice)
        self.assertNotIn("operation finished", self.session.notice)
        self.assertEqual(self.events, ["started", "stopped", "failed"])

    def test_failed_insertion_is_retained_and_blocks_overwrite(self):
        self.session.stream_delivered = lambda value, target, done: done(False)
        self.session.record_continuous(target=object())
        self.drain()
        self.assertEqual(self.session.pending_text, "Hello, world. Next…")
        self.session.record()
        self.assertIn("uninserted", self.session.notice)
        self.assertEqual(self.session.result.text, self.session.pending_text)
        self.assertEqual(self.events, ["started", "stopped", "attention"])

    def test_cancel_retains_already_inserted_text_and_stops_next_phrase(self):
        def deliver(value, target, done):
            self.sent.append(value)
            done(True)
            self.session.cancel()

        self.session.stream_delivered = deliver
        self.session.record_continuous(target=object())
        self.drain()
        self.assertEqual(self.sent, ["Hello, world"])
        self.assertEqual(self.session.result.text, "Hello, world")
        self.assertIn("already inserted", self.session.notice)
        self.assertEqual(len(self.worker.requests), 1)
        self.assertEqual(self.events, ["started", "stopped", "cancelled"])

    def test_remote_uses_selected_backend_for_each_phrase(self):
        self.session.preferences.backend = "openai"
        self.session.credential_available = True
        with patch("dictadesk.core.session.read_key", return_value="fixture-key"):
            self.session.record_continuous(target=object())
            self.drain()
        self.assertEqual(self.worker.requests, ["cloud"] * 3)

    def test_no_destination_keeps_text_without_paste(self):
        self.session.record_continuous()
        self.drain()
        self.assertEqual(self.sent, [])
        self.assertEqual(self.session.pending_text, "Hello, world. Next…")

    def test_later_recognition_failure_preserves_committed_text(self):
        original = self.worker.request

        def request(payload, cancel, **kwargs):
            if self.worker.requests:
                raise RuntimeError("fixture recognition failure")
            return original(payload, cancel, **kwargs)

        self.worker.request = request
        self.session.record_continuous(target=object())
        self.drain()
        self.assertEqual(self.sent, ["Hello, world"])
        self.assertEqual(self.session.result.text, "Hello, world")
        self.assertIn("recognition failure", self.session.notice)

    def test_notification_failure_does_not_interrupt_dictation(self):
        def unavailable(*args):
            raise RuntimeError("No notification server")

        self.session.recording_event = unavailable
        self.session.record_continuous(target=object())
        self.drain()
        self.assertEqual(self.session.result.text, "Hello, world. Next…")
        self.assertEqual(self.session.notice, "")

    def test_non_recording_and_stale_events_are_ignored(self):
        c = self.session
        c._begin("loading")
        c._recording_event(c.operation, "started")
        self.assertEqual(self.events, [])
        c._dictation_operation = c.operation
        c._recording_event(c.operation - 1, "started")
        c._recording_event(c.operation, "completed")
        self.assertEqual(self.events, [])
        c._recording_event(c.operation, "started")
        c._recording_event(c.operation, "started")
        c._recording_event(c.operation, "completed")
        c._recording_event(c.operation, "stopped")
        self.assertEqual(self.events, ["started", "stopped", "completed"])

    def test_attention_prevents_later_success_for_same_recording(self):
        c = self.session
        c._begin("starting")
        c._dictation_operation = c.operation
        c._recording_event(c.operation, "started")
        c._recording_event(c.operation, "attention")
        c._recording_event(c.operation, "completed")
        self.assertEqual(self.events, ["started", "stopped", "attention"])
