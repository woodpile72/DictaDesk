# SPDX-License-Identifier: GPL-3.0-only
from pathlib import Path
import queue
import tempfile
import threading
import unittest
from unittest.mock import patch

from dictadesk.audio.continuous import AudioUpdate, IncrementalCapture, Phrases
from dictadesk.core.incremental import StablePrefix
from dictadesk.core.session import Session
from dictadesk.core.settings import Preferences, SettingsStore
from dictadesk.core.transcript import Transcript
from test_continuous import VOICE


class AgreementTests(unittest.TestCase):
    def test_preview_then_agreed_prefix_then_final_suffix(self):
        stable = StablePrefix()
        self.assertEqual(
            stable.update("One two three four five"), ("One two three four five", False)
        )
        self.assertEqual(stable.committed, "")
        self.assertEqual(stable.update("One two three four five six"), ("four five six", False))
        self.assertEqual(stable.committed, "One two three")
        self.assertEqual(stable.update("One two three four five six.", final=True), ("", False))
        self.assertEqual(stable.committed, "One two three four five six.")

    def test_revised_ending_can_change_without_insertion(self):
        stable = StablePrefix()
        stable.update("The cat sat on the mat")
        stable.update("The cat sat on the rug")
        self.assertEqual(stable.committed, "The cat sat")
        stable.update("The cat sat beside the rug", final=True)
        self.assertEqual(stable.committed, "The cat sat beside the rug")

    def test_revision_of_committed_words_is_detected(self):
        stable = StablePrefix()
        stable.update("The cat sat on the mat")
        stable.update("The cat sat on the mat")
        self.assertTrue(stable.update("The dog sat on the mat", final=True)[1])
        self.assertEqual(stable.committed, "The cat sat")

    def test_punctuation_and_vocabulary_transform_before_agreement(self):
        stable = StablePrefix()
        for raw in (
            "hello open",
            "hello open parenthesis eric said hello again today",
            "hello open parenthesis eric said hello again today close parenthesis period",
        ):
            value = Transcript()
            value.replace(raw, [["eric", "Erick"]], True)
            stable.update(value.text)
        self.assertEqual(stable.committed, "hello (Erick said")
        stable.update(value.text, final=True)
        self.assertEqual(stable.committed, "hello (Erick said hello again today).")


class MailboxTests(unittest.TestCase):
    def test_optional_updates_coalesce_finals_remain_ordered(self):
        capture = IncrementalCapture(Preferences(), Path("unused"))
        capture.splitter = Phrases(0.8, 0.015)
        capture.splitter.audio.extend(VOICE * 15)
        capture.emit(None)
        capture.splitter.audio.extend(VOICE * 15)
        capture.emit(None)
        self.assertEqual(capture.next_update().audio, VOICE * 30)
        capture.splitter.audio.extend(VOICE * 15)
        capture.emit(None)
        capture.emit(VOICE * 50)
        capture.splitter.audio[:] = VOICE * 20
        capture.emit(None)
        self.assertEqual(capture.next_update(), AudioUpdate(0, VOICE * 50, True))
        self.assertEqual(capture.next_update(), AudioUpdate(1, VOICE * 20, False))
        self.assertFalse(capture.has_updates())

    def test_cancel_releases_full_final_queue(self):
        capture = IncrementalCapture(Preferences(), Path("unused"))
        for _ in range(8):
            capture.emit(VOICE)
        task = threading.Thread(target=capture.emit, args=(VOICE,))
        task.start()
        capture.cancel.set()
        task.join(1)
        self.assertFalse(task.is_alive())
        self.assertEqual(len(capture.finals), 8)


class IncrementalSessionTests(unittest.TestCase):
    def run_session(
        self,
        replies,
        deliver=True,
        cancel_after_insert=False,
        in_window=False,
        cancel_draft=False,
        pause_updates=False,
        pause_mode=False,
        spoken_punctuation=False,
    ):
        updates = queue.Queue()
        for i, _ in enumerate(replies):
            updates.put(
                AudioUpdate(
                    0,
                    VOICE * 2,
                    i == len(replies) - 1,
                    paused=pause_updates and i < len(replies) - 1,
                )
            )

        class Capture(IncrementalCapture):
            def run(self, started):
                started()
                self.done.set()

            def has_updates(self):
                return not updates.empty()

            def next_update(self, timeout=0.1):
                return updates.get(timeout=timeout)

        class Recognizer:
            loaded = True

            def request(self, *_args, **_kwargs):
                return {"text": next(responses)}

        responses = iter(replies)
        with (
            tempfile.TemporaryDirectory() as folder,
            patch.dict("os.environ", {"XDG_CACHE_HOME": folder}),
            patch("dictadesk.audio.continuous.IncrementalCapture", Capture),
            patch("dictadesk.audio.continuous.ContinuousCapture", Capture),
        ):
            callbacks, sent, previews, copied = queue.Queue(), [], [], []

            def changed():
                previews.append(session.inline_text if in_window else session.preview)
                if in_window and session.in_window:
                    self.assertFalse(copied, "Clipboard changed during recording")
                    if cancel_draft and session.inline_text and session.phase != "cancelling":
                        session.cancel()

            session = Session(
                callbacks.put,
                changed,
                (lambda text, target: copied.append((text, target)))
                if in_window
                else (lambda *_: self.fail("Whole transcript delivery")),
                SettingsStore(Path(folder) / "settings"),
                Recognizer(),
            )
            session.microphone_problem = ""
            session.preferences.spoken_punctuation = spoken_punctuation

            def inserted(text, target, done):
                if deliver:
                    sent.append(text)
                done(deliver)
                if cancel_after_insert:
                    session.cancel()

            session.stream_delivered = inserted
            if in_window:
                session.record_in_window()
            else:
                session.record_continuous(target=object(), pause_mode=pause_mode)
            for _ in range(100):
                try:
                    callbacks.get(timeout=0.05)()
                except queue.Empty:
                    pass
                if not session.thread.is_alive() and callbacks.empty():
                    break
            self.assertFalse(session.thread.is_alive())
            self.assertEqual(list(Path(folder).rglob("*.wav")), [])
            if in_window:
                self.assertEqual(sent, [])
                self.assertEqual(copied, [] if cancel_draft else [(session.result.text, None)])
                self.assertFalse(session.pending_text)
            return session, sent, previews

    def test_thinking_pause_does_not_insert_period_or_duplicate_text(self):
        for pause_mode in (True, False):
            c, sent, _ = self.run_session(
                ["I'm using Dictadesk.", "I'm using Dictadesk to write this comment."],
                pause_updates=True,
                pause_mode=pause_mode,
            )
            self.assertEqual(sent, ["I'm", " using DictaDesk to write this comment."])
            self.assertEqual(c.result.text, "I'm using DictaDesk to write this comment.")

    def test_long_pause_finalizes_only_the_pending_ending(self):
        c, sent, _ = self.run_session(
            ["This works.", "This works."],
            pause_updates=True,
            pause_mode=True,
        )
        self.assertEqual(sent, ["This", " works."])
        self.assertEqual(c.result.text, "This works.")

    def test_explicit_period_finishes_at_short_pause(self):
        c, sent, _ = self.run_session(
            ["This works period.", "This works period."],
            pause_updates=True,
            pause_mode=True,
            spoken_punctuation=True,
        )
        self.assertEqual("".join(sent), "This works.")
        self.assertEqual(sent[0], "This works.")
        self.assertEqual(c.result.text, "This works.")

    def test_only_stable_prefix_and_final_suffix_are_delivered(self):
        c, sent, previews = self.run_session(
            [
                "One two three four five",
                "One two three four five six",
                "One two three four five six.",
            ]
        )
        self.assertEqual(sent, ["One two three", " four five six."])
        self.assertEqual(c.result.text, "".join(sent))
        self.assertIn("One two three four five", previews)
        self.assertIn("four five six", previews)
        self.assertFalse(c.pending_text)

    def test_focus_loss_retains_only_uninserted_text_for_recovery(self):
        c, sent, _ = self.run_session(
            [
                "One two three four five",
                "One two three four five six",
                "One two three four five six.",
            ],
            deliver=False,
        )
        self.assertEqual(sent, [])
        self.assertEqual(c.pending_text, "One two three four five six.")

    def test_committed_revision_stops_insertion_and_retains_review_text(self):
        c, sent, _ = self.run_session(
            ["The cat sat on the mat", "The cat sat on the mat", "The dog sat on the mat."]
        )
        self.assertEqual(sent, ["The cat sat"])
        self.assertEqual(c.result.text, "The dog sat on the mat.")
        self.assertIn("Review Last Transcript", c.notice)
        self.assertFalse(c.pending_text)

    def test_cancel_keeps_inserted_prefix_without_final_suffix(self):
        c, sent, _ = self.run_session(
            [
                "One two three four five",
                "One two three four five six",
                "One two three four five six.",
            ],
            cancel_after_insert=True,
        )
        self.assertEqual(sent, ["One two three"])
        self.assertEqual(c.result.text, "One two three")
        self.assertFalse(c.preview)

    def test_window_draft_can_revise_without_inserting_and_copies_once_at_finish(self):
        c, sent, drafts = self.run_session(
            ["The cat sat on the mat", "The cat sat on the mat", "The dog sat on the mat."],
            in_window=True,
        )
        self.assertIn("The cat sat on the mat", drafts)
        self.assertEqual(c.inline_text, "The dog sat on the mat.")
        self.assertEqual(c.result.text, c.inline_text)
        self.assertNotIn("Review Last Transcript", c.notice)
        self.assertFalse(c.in_window)

    def test_window_cancel_does_not_copy_partial_text(self):
        c, sent, drafts = self.run_session(
            ["A changing draft", "A finished sentence."], in_window=True, cancel_draft=True
        )
        self.assertIn("A changing draft", drafts)
        self.assertFalse(c.in_window)
