# SPDX-License-Identifier: GPL-3.0-only
"""Microphone samples use selected settings, without changing active dictation."""

from dataclasses import replace
from pathlib import Path
import queue
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from dictadesk.core.session import Session
from dictadesk.core.settings import SettingsStore


class Worker:
    def __init__(self, loaded=False):
        self.loaded = loaded
        self.requests = []
        self.closed = False

    def request(self, payload, cancel, **kwargs):
        if cancel.is_set():
            raise InterruptedError()
        self.requests.append(payload)
        if payload["op"] == "load":
            self.loaded = True
            return {"device": "cpu"}
        return {"text": "sample speech"}

    def close(self):
        self.closed = True
        self.loaded = False


class SampleCapture:
    seen = []

    def __init__(self, prefs, directory, seconds=None):
        self.prefs, self.directory = prefs, Path(directory)
        self.stop, self.cancel, self.done = (
            threading.Event(),
            threading.Event(),
            threading.Event(),
        )
        self.peak, self.level = 0.5, 0.0
        self.seen.append(prefs)

    def run(self, started):
        started()
        path = self.directory / "sample.wav"
        path.write_bytes(b"fixture")
        return path


class SampleTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.callbacks = queue.Queue()
        self.main = Worker()
        self.deliveries = []
        self.session = Session(
            self.callbacks.put,
            lambda: None,
            lambda *args: self.deliveries.append(args),
            SettingsStore(Path(self.folder.name) / "prefs"),
            self.main,
        )
        self.session.microphone_problem = "The saved microphone is missing"
        self.session.result.replace("Keep dictation", [])
        self.session.last_dictation = "Keep completed dictation"
        self.session.pending_text = "Keep recovery text"
        self.settings = self.session.preferences
        self.draft = replace(self.settings, microphone="Selected microphone", host_api="ALSA")
        SampleCapture.seen = []
        for target, kwargs in (
            ("dictadesk.core.session.Capture", {"new": SampleCapture}),
            ("dictadesk.core.session.check", {"return_value": None}),
            ("dictadesk.core.session.downloaded", {"return_value": True}),
        ):
            mocked = patch(target, **kwargs)
            mocked.start()
            self.addCleanup(mocked.stop)
        env = patch.dict("os.environ", {"XDG_CACHE_HOME": self.folder.name})
        env.start()
        self.addCleanup(env.stop)

    def drain(self):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                self.callbacks.get(timeout=0.02)()
            except queue.Empty:
                pass
            if not self.session.thread.is_alive() and self.callbacks.empty():
                break
        self.assertFalse(self.session.thread.is_alive())
        self.assertEqual(self.session.phase, "idle")

    def assert_retained(self):
        self.assertIs(self.session.preferences, self.settings)
        self.assertEqual(self.session.result.text, "Keep dictation")
        self.assertEqual(self.session.last_dictation, "Keep completed dictation")
        self.assertEqual(self.session.pending_text, "Keep recovery text")
        self.assertFalse(self.deliveries)

    def test_sample_loads_installed_model_and_uses_unsaved_microphone(self):
        self.assertFalse(self.session.can_record)
        self.session.record(sample=True, preferences=self.draft)
        self.drain()
        self.assertEqual([p["op"] for p in self.main.requests], ["load", "transcribe"])
        self.assertTrue(self.main.requests[0]["local_only"])
        self.assertEqual(SampleCapture.seen[-1].microphone, "Selected microphone")
        self.assertEqual(self.session.sample_text, "sample speech")
        self.assert_retained()

    def test_alternate_model_is_temporary_and_can_be_tested_repeatedly(self):
        self.main.loaded = True
        temporary = []

        def worker():
            instance = Worker()
            temporary.append(instance)
            return instance

        with patch("dictadesk.core.session.Recognizer", side_effect=worker):
            for model, microphone in [
                ("whisper-base", "First input"),
                ("whisper-tiny", "Second input"),
            ]:
                self.session.record(
                    sample=True,
                    preferences=replace(self.draft, local_model=model, microphone=microphone),
                )
                self.drain()
                self.assertEqual(temporary[-1].requests[0]["local_model"], model)
                self.assertTrue(temporary[-1].closed)
                self.assertEqual(SampleCapture.seen[-1].microphone, microphone)
        self.assertTrue(self.main.loaded)
        self.assertFalse(self.main.closed)
        self.assert_retained()

    def test_failed_input_does_not_disable_testing_another_input(self):
        with patch("dictadesk.core.session.check", side_effect=ValueError("Input unavailable")):
            self.session.record(sample=True, preferences=self.draft)
            self.drain()
        self.assertIn("Input unavailable", self.session.notice)
        self.session.record(
            sample=True, preferences=replace(self.draft, microphone="Working input")
        )
        self.drain()
        self.assertEqual(self.session.sample_text, "sample speech")
        self.assert_retained()

    def test_missing_model_does_not_start_capture_or_download_implicitly(self):
        with (
            patch("dictadesk.core.session.downloaded", return_value=False),
            patch.object(SampleCapture, "run") as capture,
        ):
            self.session.record(sample=True, preferences=self.draft)
            self.drain()
            capture.assert_not_called()
        self.assertIn("Download", self.session.notice)
        self.assertFalse(self.main.requests)
        self.assert_retained()

    def test_remote_sample_can_use_the_unsaved_key_without_saving_it(self):
        self.main.loaded = True
        with patch(
            "dictadesk.core.session.read_key",
            side_effect=AssertionError("Should use selected key"),
        ):
            self.session.record(
                sample=True,
                preferences=replace(self.draft, backend="openai"),
                api_key="fixture-key",
            )
            self.drain()
        self.assertEqual(self.main.requests[-1]["op"], "cloud")
        self.assertEqual(self.main.requests[-1]["key"], "fixture-key")
        self.assert_retained()
