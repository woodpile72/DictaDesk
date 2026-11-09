# SPDX-License-Identifier: GPL-3.0-only
"""Optional downloads, configuration, output cancellation and dictation isolation."""

import base64
from dataclasses import replace
import hashlib
import io
import json
from pathlib import Path
import queue
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import zipfile

from dictadesk.audio.speech import SpeechOutput, play
from dictadesk.core.settings import Preferences, SettingsStore
from dictadesk.core.session import Session
from dictadesk.core.tts import Artifact, SpeechStore, Voice, download, unpack


class SpeechStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.store = SpeechStore(self.root / "tts")
        self.payloads = {}
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as wheel:
            wheel.writestr("piper/__init__.py", "# fixture")
            wheel.writestr("piper/espeakbridge.so", b"fixture native")
        runtime = self.artifact("runtime.whl", archive.getvalue())
        voice = self.artifact("fixture.onnx", b"fixture voice")
        config = self.artifact("fixture.onnx.json", b"{}")
        for target, value in (
            ("dictadesk.core.tts.RUNTIME", (runtime,)),
            (
                "dictadesk.core.tts.VOICES",
                {"fixture": Voice("piper", "Fixture", "", (voice, config))},
            ),
        ):
            mocked = patch(target, value)
            mocked.start()
            self.addCleanup(mocked.stop)
        original = download

        def local(artifact, path, cancel, progress):
            return original(
                artifact,
                path,
                cancel,
                progress,
                fetch=lambda request, **kwargs: io.BytesIO(self.payloads[request.full_url]),
            )

        mocked = patch("dictadesk.core.tts.download", side_effect=local)
        mocked.start()
        self.addCleanup(mocked.stop)

    def artifact(self, name, data):
        url = "https://fixture.invalid/" + name
        self.payloads[url] = data
        return Artifact(url, len(data), hashlib.sha256(data).hexdigest(), name)

    def test_verified_install_reuses_engine_and_is_independently_removable(self):
        asr = self.root / "dictation-model.onnx"
        asr.write_bytes(b"keep")
        self.store.install("fixture", threading.Event())
        self.assertTrue(self.store.ready("piper", "fixture"))
        self.store.verify("piper", "fixture")
        self.payloads.clear()  # A second install must use the verified local files.
        self.store.install("fixture", threading.Event())
        self.store.remove()
        self.assertEqual(asr.read_bytes(), b"keep")
        self.assertFalse(self.store.root().exists())

    def test_cancel_and_hash_failure_never_publish_partial_model(self):
        stop = threading.Event()
        stop.set()
        with self.assertRaises(InterruptedError):
            self.store.install("fixture", stop)
        self.assertFalse(self.store.ready("piper", "fixture"))
        self.assertFalse(list(self.store.root().glob(".speech-download-*")))
        self.payloads["https://fixture.invalid/runtime.whl"] = b"broken"
        with self.assertRaisesRegex(ValueError, "verification"):
            self.store.install("fixture", threading.Event())
        self.assertFalse(self.store.ready("piper", "fixture"))
        self.assertFalse(list(self.store.root().glob(".speech-download-*")))

    def test_corrupted_or_linked_files_are_rejected_before_import(self):
        self.store.install("fixture", threading.Event())
        voice = self.store.voice_folder("fixture") / "fixture.onnx"
        voice.write_bytes(b"altered voice")
        with self.assertRaises(ValueError):
            self.store.verify("piper", "fixture")
        self.store.remove()
        root = self.store.root(create=True)
        (root / "voices").symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symbolic"):
            self.store.voice_folder("fixture")

    def test_wheel_extraction_rejects_traversal_and_executable_path_hooks(self):
        for member in ("../outside", "/absolute", "unexpected.pth"):
            path = self.root / "unsafe.whl"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr(member, "payload")
            with self.assertRaisesRegex(ValueError, "unsafe"):
                unpack(path, self.root / "unpacked")
        self.assertFalse((self.root / "outside").exists())

    def test_private_cache_permissions_are_required(self):
        self.store.root(create=True).chmod(0o755)
        with self.assertRaisesRegex(ValueError, "permissions"):
            self.store.root()


class Worker:
    def __init__(self):
        self.loaded = False
        self.requests = []

    def close(self):
        self.loaded = False

    def request(self, payload, cancelled, **kwargs):
        if cancelled.is_set():
            raise InterruptedError()
        self.requests.append(payload)
        if payload["op"] == "load":
            self.loaded = True
            return {"loaded": True}
        return {"rate": 16000, "audio": base64.b64encode(b"\x01\x01" * 4000).decode()}


class SpeechOutputTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.store = Mock()
        self.store.root.return_value = Path(self.folder.name)
        self.store.ready.return_value = True
        self.worker = Worker()
        self.output = Mock()
        self.speech = SpeechOutput(
            lambda f: f(),
            lambda: None,
            store=self.store,
            worker=self.worker,
            output=self.output,
        )
        self.addCleanup(self.cleanup)

    def cleanup(self):
        finished = threading.Event()
        self.speech.close(finished.set)
        self.assertTrue(finished.wait(3))

    def test_disabled_default_has_no_download_model_or_worker_activity(self):
        self.speech.tick(Preferences())
        self.assertEqual(self.worker.requests, [])
        self.store.ready.assert_not_called()
        self.store.install.assert_not_called()
        self.assertFalse(self.worker.loaded)

    def test_acknowledgment_uses_configured_text_and_reuses_loaded_voice(self):
        prefs = replace(Preferences(), tts_enabled=True, tts_phrase="Hello there")
        for _ in range(2):
            self.assertTrue(self.speech.acknowledge(prefs, threading.Event()))
        self.assertEqual(
            [r["op"] for r in self.worker.requests], ["load", "synthesize", "synthesize"]
        )
        self.assertEqual(self.worker.requests[-1]["text"], "Hello there")
        self.assertEqual(self.output.call_count, 2)
        self.speech.tick(replace(prefs, tts_enabled=False))
        self.speech.prepare_thread.join(2)
        self.assertFalse(self.worker.loaded)

    def test_timer_confirmation_synthesizes_requested_duration_without_saving_phrase(self):
        original = Preferences(tts_enabled=True)
        prefs = replace(original, tts_phrase="A timer has been set for 24 minutes.")
        self.speech.preview(prefs, confirmation=True)
        self.speech.preview_thread.join(2)
        self.assertFalse(self.speech.previewing)
        self.assertFalse(self.speech.confirming)
        self.assertEqual(self.worker.requests[-1]["text"], prefs.tts_phrase)
        self.assertEqual(original.tts_phrase, "Hello")
        self.assertEqual(self.speech.notice, "Timer confirmation finished.")
        self.output.assert_called_once()

    def test_unavailable_voice_and_output_errors_are_nonblocking(self):
        self.store.ready.return_value = False
        self.assertFalse(self.speech.acknowledge(Preferences(), threading.Event()))
        self.assertEqual(self.worker.requests, [])
        self.store.ready.return_value = True
        self.output.side_effect = RuntimeError("speaker unavailable")
        self.assertFalse(self.speech.acknowledge(Preferences(), threading.Event()))
        self.assertIn("speaker unavailable", self.speech.problem)
        self.assertFalse(self.speech.speaking)
        self.output.side_effect = None
        self.assertTrue(self.speech.acknowledge(Preferences(), threading.Event()))

    def test_download_never_blocks_acknowledgment_or_touches_audio(self):
        started, release = threading.Event(), threading.Event()

        def install(*_):
            started.set()
            release.wait(2)

        self.store.install.side_effect = install
        self.speech.download("en_US-lessac-medium")
        self.assertTrue(started.wait(1))
        self.assertFalse(self.speech.acknowledge(Preferences(), threading.Event()))
        self.assertEqual(self.worker.requests, [])
        self.output.assert_not_called()
        release.set()
        self.speech.download_thread.join(2)
        self.assertFalse(self.speech.downloading)

    def test_starting_an_operation_stops_and_joins_preview_before_work(self):
        started, active = threading.Event(), threading.Event()

        def output(rate, data, stop):
            active.set()
            started.set()
            try:
                if stop.wait(2):
                    raise InterruptedError()
            finally:
                active.clear()

        self.speech.output = output
        self.speech.preview(Preferences())
        self.assertTrue(started.wait(1))
        callbacks = queue.Queue()
        session = Session(
            callbacks.put,
            lambda: None,
            lambda *_: None,
            store=SettingsStore(Path(self.folder.name) / "settings"),
            recognizer=Worker(),
        )
        session.before_operation = self.speech.stop_playback
        session.wait_for_output = self.speech.wait_playback
        ticket = session._begin("loading")
        session._run(ticket, lambda *_: self.assertFalse(active.is_set()), lambda *_: None)
        session.thread.join(2)
        self.assertFalse(session.thread.is_alive())
        while not callbacks.empty():
            callbacks.get_nowait()()
        self.assertEqual(session.phase, "idle")
        self.assertFalse(self.speech.previewing)

    def test_cleanup_failure_does_not_prevent_shutdown_callback(self):
        self.worker.close = Mock(side_effect=RuntimeError("close failed"))
        finished = threading.Event()
        self.speech.close(finished.set)
        self.assertTrue(finished.wait(2))
        self.worker.close = Mock()


class PlaybackTests(unittest.TestCase):
    def test_cancelled_output_closes_even_if_abort_fails(self):
        stop = threading.Event()
        stream = Mock()
        stream.write.side_effect = lambda *_: stop.set()
        stream.abort.side_effect = RuntimeError("abort failed")
        with patch("sounddevice.RawOutputStream", return_value=stream):
            with self.assertRaises(RuntimeError):
                play(16000, b"\x01\x01" * 16000, stop)
        stream.close.assert_called_once()

    def test_precancelled_output_never_opens_a_device(self):
        stop = threading.Event()
        stop.set()
        with patch("sounddevice.RawOutputStream") as output:
            with self.assertRaises(InterruptedError):
                play(16000, b"\x01\x01" * 1600, stop)
            output.assert_not_called()


class SpeechSettingsTests(unittest.TestCase):
    def test_defaults_validation_and_backward_compatible_read(self):
        prefs = Preferences().validate()
        self.assertFalse(prefs.tts_enabled)
        self.assertEqual(prefs.tts_phrase, "Hello")
        for changes in (
            {"tts_enabled": 1},
            {"tts_phrase": ""},
            {"tts_phrase": "a\nb"},
            {"tts_phrase": "a" * 121},
            {"tts_engine": "shell"},
            {"tts_engine": "espeak", "tts_model": "en_US-lessac-medium"},
        ):
            with self.assertRaises(ValueError):
                replace(prefs, **changes).validate()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings"
            path.write_text(
                json.dumps({"schema": 1, "preferences": {"wake_phrase": "Hey Clanker"}})
            )
            loaded = SettingsStore(path).read()
            self.assertEqual(loaded.wake_phrase, "Computer")
            self.assertFalse(loaded.tts_enabled)
            self.assertEqual(loaded.tts_phrase, "Hello")
