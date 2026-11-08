# SPDX-License-Identifier: GPL-3.0-only
from dataclasses import replace
from pathlib import Path
import json
import os
import queue
import stat
import sys
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from dictadesk.core.settings import Preferences, SettingsStore, private_write
from dictadesk.core.transcript import Transcript, correct
from dictadesk.core.session import Session
from dictadesk.desktop.keyboard import Chord
from dictadesk.audio.service import Recognizer


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "settings.json"
        self.store = SettingsStore(self.path)

    def test_defaults_and_roundtrip(self):
        self.assertEqual(self.store.read().shortcut, "<Super>v")
        self.assertEqual(self.store.read().backend, "local")
        self.assertEqual(self.store.read().local_model, "parakeet")
        self.assertEqual(self.store.read().cloud_model, "gpt-transcribe")
        p = Preferences(vocabulary=[["eric", "Erick"]], spoken_punctuation=True)
        self.store.save(p)
        self.assertEqual(self.store.read(), p)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_old_default_wake_phrases_upgrade_without_other_changes(self):
        from dataclasses import asdict

        for phrase in ("Clanker", "Hey Clanker", " clanker ", "HEY  CLANKER"):
            with self.subTest(phrase=phrase):
                old = Preferences(
                    wake_phrase=phrase,
                    hands_free=True,
                    volume_step=7,
                    microphone="Laptop Mic",
                    tts_enabled=True,
                    tts_phrase="Ready",
                    vocabulary=[["eric", "Erick"]],
                )
                raw = json.dumps({"schema": 1, "preferences": asdict(old)}).encode()
                self.path.write_bytes(raw)
                current = self.store.read()
                self.assertFalse(self.store.problem)
                self.assertEqual(current, replace(old, wake_phrase="Computer"))
                self.assertEqual(self.path.read_bytes(), raw)
                self.store.save(current)
                self.assertEqual(self.store.read(), current)

    def test_custom_wake_phrases_remain_configurable_after_migration(self):
        for phrase in ("Hello Desk", "Computer", "Clanker", "Hey Clanker"):
            with self.subTest(phrase=phrase):
                prefs = Preferences(wake_phrase=phrase)
                self.store.save(prefs)
                self.assertEqual(self.store.read(), prefs)
        self.path.write_text(
            json.dumps({"schema": 1, "preferences": {"wake_phrase": "Hello Desk"}})
        )
        self.assertEqual(self.store.read().wake_phrase, "Hello Desk")

    def test_existing_pause_setting_gets_separate_sentence_default(self):
        self.path.write_text(json.dumps({"schema": 1, "preferences": {"pause_seconds": 0.8}}))
        prefs = self.store.read()
        self.assertEqual(prefs.pause_seconds, 0.8)
        self.assertEqual(prefs.sentence_pause_seconds, 4.0)
        prefs.sentence_pause_seconds = 5.2
        self.store.save(prefs)
        self.assertEqual(self.store.read().sentence_pause_seconds, 5.2)
        with self.assertRaises(ValueError):
            replace(prefs, sentence_pause_seconds=0.2).validate()

    def test_silence_stop_defaults_and_custom_values_survive_save(self):
        self.path.write_text(
            json.dumps({"schema": 1, "preferences": {"sentence_pause_seconds": 4.5}})
        )
        prefs = self.store.read()
        self.assertEqual(prefs.silence_stop_seconds, 20)
        self.assertEqual(prefs.sentence_pause_seconds, 4.5)
        for value in (0, 3, 45, 600):
            self.store.save(replace(prefs, silence_stop_seconds=value))
            self.assertEqual(self.store.read().silence_stop_seconds, value)
        for value in (-1, 601, True, 1.5):
            with self.assertRaises(ValueError):
                replace(prefs, silence_stop_seconds=value).validate()

    def test_legacy_remote_models_upgrade_without_resetting_other_preferences(self):
        from dataclasses import asdict
        from dictadesk.core.settings import LEGACY_REMOTE_MODELS

        for backend in ("local", "openai"):
            for model in LEGACY_REMOTE_MODELS:
                with self.subTest(backend=backend, model=model):
                    values = asdict(
                        Preferences(
                            backend=backend,
                            cloud_model=model,
                            local_model="moonshine-tiny",
                            language="fr",
                            vocabulary=[["eric", "Erick"]],
                            autostart=True,
                        )
                    )
                    raw = json.dumps({"schema": 1, "preferences": values}).encode()
                    self.path.write_bytes(raw)
                    prefs = self.store.read()
                    self.assertFalse(self.store.problem)
                    self.assertEqual(asdict(prefs), {**values, "cloud_model": "gpt-transcribe"})
                    self.assertEqual(self.path.read_bytes(), raw)
                    self.store.save(prefs)
                    self.assertEqual(self.store.read(), prefs)
                    self.assertEqual(
                        json.loads(self.path.read_bytes())["preferences"]["cloud_model"],
                        "gpt-transcribe",
                    )

    def test_unknown_remote_model_still_needs_recovery(self):
        raw = json.dumps(
            {"schema": 1, "preferences": {"cloud_model": "gpt-live-transcribe"}}
        ).encode()
        self.path.write_bytes(raw)
        self.store.read()
        self.assertTrue(self.store.problem)
        self.assertEqual(self.store.damaged, raw)
        self.assertEqual(self.path.read_bytes(), raw)

    def test_legacy_shortcut_modes_retire_without_resetting_preferences(self):
        for old_mode in ("hold", "toggle"):
            with self.subTest(mode=old_mode):
                raw = json.dumps(
                    {
                        "schema": 1,
                        "preferences": {
                            "mode": old_mode,
                            "shortcut": "<Super>m",
                            "pause_seconds": 1.2,
                            "vocabulary": [["eric", "Erick"]],
                            "spoken_punctuation": True,
                        },
                    }
                ).encode()
                self.path.write_bytes(raw)
                prefs = self.store.read()
                self.assertFalse(self.store.problem)
                self.assertFalse(hasattr(prefs, "mode"))
                self.assertEqual(prefs.shortcut, "<Super>m")
                self.assertEqual(prefs.pause_seconds, 1.2)
                self.assertEqual(prefs.vocabulary, [["eric", "Erick"]])
                self.assertTrue(prefs.spoken_punctuation)
                self.assertEqual(self.path.read_bytes(), raw)
                self.store.save(prefs)
                self.assertNotIn("mode", json.loads(self.path.read_text())["preferences"])

    def test_invalid_legacy_mode_still_requires_recovery(self):
        self.path.write_text(json.dumps({"schema": 1, "preferences": {"mode": "broken"}}))
        self.store.read()
        self.assertTrue(self.store.problem)

    def test_failed_replace_keeps_previous_bytes(self):
        self.store.save(Preferences())
        original = self.path.read_bytes()
        with patch("os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.store.save(Preferences(max_seconds=60))
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.path.parent.glob(".pending-*")), [])

    def test_recovery_backs_up_exact_bytes(self):
        raw = b"broken\x00\xff"
        self.path.write_bytes(raw)
        self.store.read()
        with self.assertRaises(ValueError):
            self.store.save(Preferences())
        backup = self.store.recover()
        self.assertEqual(backup.read_bytes(), raw)
        self.assertEqual(self.store.read(), Preferences())
        self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)

    def test_changed_damaged_file_not_replaced(self):
        self.path.write_text("broken")
        self.store.read()
        self.path.write_text("changed")
        with self.assertRaises(ValueError):
            self.store.recover()
        self.assertEqual(self.path.read_text(), "changed")

    def test_recovery_save_failure_retains_problem_and_backup(self):
        self.path.write_text("broken")
        self.store.read()
        with patch("os.replace", side_effect=OSError("no space")):
            with self.assertRaises(OSError):
                self.store.recover()
        self.assertTrue(self.store.problem)
        self.assertEqual(self.path.read_text(), "broken")
        self.assertEqual(len(list(self.path.parent.glob("preferences.broken-*"))), 1)

    def test_no_secret_or_transcript_in_settings(self):
        self.store.save(Preferences())
        content = self.path.read_text()
        self.assertNotIn("api_key", content)
        self.assertNotIn("transcript", content)

    def test_invalid_schema_preserved(self):
        self.path.write_text(json.dumps({"schema": 99}))
        self.store.read()
        self.assertTrue(self.store.problem)
        self.assertTrue(self.store.damaged)

    def test_invalid_settings_rejected(self):
        cases = {
            "backend": "other",
            "max_seconds": -1,
            "idle_seconds": True,
            "auto_paste": "yes",
            "shortcut": "v",
            "paste_shortcut": "<Super>v",
            "vocabulary": [["x", "a"], ["X", "b"]],
            "language": "hello\nworld",
            "quantization": "bad",
            "local_model": "unknown",
            "cloud_model": "unknown",
        }
        for key, value in cases.items():
            with self.subTest(key=key), self.assertRaises(ValueError):
                replace(Preferences(), **{key: value}).validate()

    def test_private_export_unicode(self):
        private_write(self.path, "Hello, 世界\n".encode())
        self.assertEqual(self.path.read_text(), "Hello, 世界\n")
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)


class TranscriptTests(unittest.TestCase):
    def test_builtin_branding_and_personal_override(self):
        self.assertEqual(
            correct("Dictadesk dictadesk DICTADESK", []), "DictaDesk DictaDesk DictaDesk"
        )
        self.assertEqual(correct("Dictadesk", [["dictadesk", "My tool"]]), "My tool")
        self.assertEqual(correct("voice", [["voice", "dictadesk"]]), "dictadesk")
        text = "dictadesking dictadesk.com https://site/dictadesk me@dictadesk.org /dictadesk"
        self.assertEqual(correct(text, []), text)
        result = Transcript()
        result.replace("Dictadesk works.", [])
        self.assertEqual(result.text, "DictaDesk works.")
        result.undo = True
        self.assertEqual(result.text, "Dictadesk works.")

    def test_whole_words_unicode_case(self):
        self.assertEqual(
            correct("ERIC Ericson éric", [["eric", "Erick"]]), "Erick Ericson éric"
        )

    def test_no_cascading(self):
        self.assertEqual(correct("a b", [["a", "b"], ["b", "c"]]), "b c")

    def test_longest_phrase_and_literal_punctuation(self):
        self.assertEqual(
            correct(
                "C++ and C plus plus",
                [["C", "see"], ["C++", "C plus plus"], ["C plus plus", "C++"]],
            ),
            "C plus plus and C++",
        )

    def test_reversible_original(self):
        result = Transcript()
        result.replace("eric", [["eric", "Erick"]])
        self.assertTrue(result.can_undo)
        result.undo = True
        self.assertEqual(result.text, "eric")
        result.undo = False
        self.assertEqual(result.text, "Erick")

    def test_no_corrections_no_undo(self):
        result = Transcript()
        result.replace("hello", [])
        self.assertFalse(result.can_undo)


class ShortcutTests(unittest.TestCase):
    def test_configured_display(self):
        self.assertEqual(str(Chord.parse("<Super>v")), "Super+V")
        self.assertEqual(str(Chord.parse("<Control><Alt>p")), "Ctrl+Alt+P")

    def test_reject_unmodified_or_unsupported(self):
        for value in (
            "v",
            "<Super>",
            "<Control><Control>v",
            "<Meta>v",
            "<Super>space",
            "<Super>é",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Chord.parse(value)


class WorkerTests(unittest.TestCase):
    def test_system_python_worker_without_development_import_paths(self):
        import dictadesk

        root = str(Path(dictadesk.__file__).resolve().parent.parent)
        script = """
import sys, threading
sys.path[:0] = [sys.argv[1]]
from dictadesk.audio.service import Recognizer
worker = Recognizer()
try:
    worker.request({'op': 'test-unsupported'}, threading.Event(), timeout=5)
except RuntimeError as error:
    assert str(error) == 'Unsupported worker operation.', str(error)
else:
    raise AssertionError('Expected the real worker protocol reply')
finally:
    worker.close()
"""
        with tempfile.TemporaryDirectory() as folder:
            subprocess.run(
                ["/usr/bin/python3", "-I", "-c", script, root],
                cwd=folder,
                env={k: v for k, v in os.environ.items() if k != "PYTHONPATH"},
                check=True,
                timeout=15,
            )

    def worker(self, script):
        worker = Recognizer([sys.executable, "-u", "-c", script])
        self.addCleanup(worker.close)
        return worker

    def test_request_response(self):
        worker = self.worker(
            "import sys,json\nfor line in sys.stdin: print(json.dumps({'text':'hello'}), flush=True)"
        )
        self.assertEqual(
            worker.request({"op": "transcribe"}, threading.Event()), {"text": "hello"}
        )

    def test_cancellation_terminates_process(self):
        worker = self.worker("import sys,time\nsys.stdin.readline()\ntime.sleep(60)")
        cancelled = threading.Event()
        timer = threading.Timer(0.2, cancelled.set)
        timer.start()
        self.addCleanup(timer.cancel)
        with self.assertRaises(InterruptedError):
            worker.request({"op": "load"}, cancelled)
        self.assertIsNone(worker.process)
        self.assertFalse(worker.loaded)

    def test_timeout_terminates_process(self):
        worker = self.worker("import time;time.sleep(60)")
        with self.assertRaises(TimeoutError):
            worker.request({"op": "load"}, threading.Event(), timeout=0.1)
        self.assertIsNone(worker.process)

    def test_large_audio_submission_can_be_cancelled_when_worker_is_not_reading(self):
        worker = self.worker("import time;time.sleep(60)")
        cancelled = threading.Event()
        timer = threading.Timer(0.1, cancelled.set)
        timer.start()
        try:
            with self.assertRaises(InterruptedError):
                worker.request(
                    {"op": "transcribe_pcm", "audio": "A" * 340000}, cancelled, timeout=2
                )
            self.assertIsNone(worker.process)
        finally:
            timer.cancel()
            timer.join()

    def test_waiting_for_inference_lock_is_cancellable_and_bounded(self):
        worker = self.worker("raise Exception('must not start')")
        worker.serial.acquire()
        try:
            with self.assertRaises(TimeoutError):
                worker.request({"op": "load"}, threading.Event(), timeout=0.01)
            cancelled = threading.Event()
            cancelled.set()
            with self.assertRaises(InterruptedError):
                worker.request({"op": "load"}, cancelled, timeout=1)
            self.assertIsNone(worker.process)
        finally:
            worker.serial.release()

    def test_invalid_protocol_fails(self):
        worker = self.worker("import sys\nsys.stdin.readline()\nprint('oops', flush=True)")
        with self.assertRaises(RuntimeError):
            worker.request({"op": "load"}, threading.Event())

    def test_error_reply_fails(self):
        worker = self.worker(
            'import sys\nsys.stdin.readline()\nprint(\'{"error":"test failure"}\', flush=True)'
        )
        with self.assertRaisesRegex(RuntimeError, "test failure"):
            worker.request({"op": "load"}, threading.Event())

    def test_early_exit_fails(self):
        worker = self.worker("pass")
        with self.assertRaises((RuntimeError, BrokenPipeError)):
            worker.request({"op": "load"}, threading.Event())

    def test_pre_cancelled_does_not_start(self):
        worker = self.worker('raise Exception("should not run")')
        event = threading.Event()
        event.set()
        with self.assertRaises(InterruptedError):
            worker.request({"op": "load"}, event)
        self.assertIsNone(worker.process)


class FakeRecognizer:
    loaded = True

    def __init__(self):
        self.reply = {"text": "eric said hello"}

    def request(self, payload, cancel, **kwargs):
        if cancel.is_set():
            raise InterruptedError()
        return self.reply

    def close(self):
        self.loaded = False


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.callbacks = queue.Queue()
        self.deliveries = []
        self.session = Session(
            self.callbacks.put,
            lambda: None,
            lambda text, target: self.deliveries.append((text, target)),
            SettingsStore(Path(self.tmp.name) / "settings"),
            FakeRecognizer(),
        )
        self.session.microphone_problem = ""

    def drain(self):
        if self.session.thread:
            self.session.thread.join(timeout=3)
            self.assertFalse(self.session.thread.is_alive())
        while not self.callbacks.empty():
            self.callbacks.get_nowait()()

    def test_not_ready_until_microphone_and_model(self):
        self.assertTrue(self.session.can_record)
        self.session.microphone_problem = "missing"
        self.assertFalse(self.session.can_record)
        self.session.microphone_problem = ""
        self.session.recognizer.loaded = False
        self.assertFalse(self.session.can_record)

    def test_installed_unloaded_model_is_standby_not_ready_or_setup(self):
        c = self.session
        c.recognizer.loaded = False
        c.preferences.local_model = "whisper-base"
        with patch("dictadesk.core.session.downloaded", return_value=True) as installed:
            self.assertEqual(c.state, "Standby")
            installed.assert_called_with(c.preferences.quantization, "whisper-base")
            self.assertFalse(c.can_record)
            c.phase = "loading"
            self.assertEqual(c.state, "Loading")
            self.assertFalse(c.can_record)
            c.phase = "idle"
            c.recognizer.loaded = True
            self.assertEqual(c.state, "Ready")
            self.assertTrue(c.can_record)

    def test_standby_does_not_hide_actual_setup_problems(self):
        c = self.session
        c.recognizer.loaded = False
        with patch("dictadesk.core.session.downloaded", return_value=False):
            self.assertEqual(c.state, "Setup needed")
        with patch("dictadesk.core.session.downloaded", return_value=True):
            c.microphone_problem = "Microphone missing"
            self.assertEqual(c.state, "Setup needed")
            c.microphone_problem = ""
            c.store.problem = "Damaged settings"
            self.assertEqual(c.state, "Setup needed")
            c.store.problem = ""
            c.preferences.backend = "openai"
            self.assertEqual(c.state, "Setup needed")

    def test_cuda_fallback_saves_cpu_and_preserves_other_preferences(self):
        c = self.session
        c.preferences.device = "cuda"
        c.preferences.vocabulary = [["eric", "Erick"]]
        c.recognizer.reply = {
            "loaded": True,
            "device": "cpu",
            "compute_notice": "CUDA unavailable. Switched to CPU.",
        }
        with patch("dictadesk.core.session.model_directory", return_value=Path(self.tmp.name)):
            c.load(local_only=True)
            self.drain()
        self.assertEqual(c.preferences.device, "cpu")
        self.assertEqual(c.store.read().device, "cpu")
        self.assertEqual(c.store.read().vocabulary, [["eric", "Erick"]])
        self.assertIn("Switched to CPU", c.notice)

    def test_cuda_fallback_save_failure_still_uses_cpu_with_warning(self):
        c = self.session
        c.preferences.device = "cuda"
        c.store.save(c.preferences)
        c.recognizer.reply = {"loaded": True, "device": "cpu"}
        with (
            patch("dictadesk.core.session.model_directory", return_value=Path(self.tmp.name)),
            patch.object(c.store, "save", side_effect=OSError("disk full")),
        ):
            c.load(local_only=True)
            self.drain()
        self.assertEqual(c.preferences.device, "cpu")
        self.assertEqual(c.store.read().device, "cuda")
        self.assertIn("Could not save", c.notice)

    def test_saving_unavailable_cuda_persists_cpu(self):
        with (
            patch(
                "dictadesk.core.session.compute_capabilities",
                return_value={"cuda": False, "reason": "CPU runtime only."},
            ),
            patch.object(self.session, "scan_devices"),
        ):
            self.session.save_preferences(
                replace(self.session.preferences, device="cuda"), lambda: None
            )
            self.drain()
        self.assertEqual(self.session.preferences.device, "cpu")
        self.assertEqual(self.session.store.read().device, "cpu")
        self.assertIn("Switched to CPU", self.session.notice)

    def test_cloud_requires_credential(self):
        self.session.preferences.backend = "openai"
        self.assertFalse(self.session.can_record)
        self.session.credential_available = True
        self.assertTrue(self.session.can_record)

    def test_startup_loads_selected_downloaded_model_without_download(self):
        self.session.recognizer.loaded = False
        self.session.preferences.quantization = "float32"
        with (
            patch("dictadesk.core.session.downloaded", return_value=True) as available,
            patch.object(self.session.recognizer, "request", return_value={}) as request,
            patch("dictadesk.core.session.model_directory", return_value=Path(self.tmp.name)),
        ):
            self.session.load_available_model()
            self.assertEqual(self.session.phase, "loading")
            self.assertFalse(self.session.can_record)
            self.drain()
        available.assert_called_once_with("float32", "parakeet")
        self.assertTrue(request.call_args.args[0]["local_only"])
        self.assertEqual(request.call_args.args[0]["precision"], "float32")

    def test_startup_skips_remote_missing_loaded_busy_or_damaged_state(self):
        for case in ("remote", "missing", "loaded", "busy", "damaged", "closing"):
            with self.subTest(case=case):
                self.session.preferences.backend = "openai" if case == "remote" else "local"
                self.session.recognizer.loaded = case == "loaded"
                self.session.phase = "loading" if case == "busy" else "idle"
                self.session.store.problem = "damaged" if case == "damaged" else ""
                self.session.closing = case == "closing"
                with (
                    patch("dictadesk.core.session.downloaded", return_value=case != "missing"),
                    patch.object(self.session, "load") as load,
                ):
                    self.session.load_available_model()
                    load.assert_not_called()

    def test_cancel_drops_late_result_preserves_previous(self):
        self.session.result.replace("previous", [])
        ticket = self.session._begin("transcribing")
        gate = threading.Event()
        self.session._run(
            ticket,
            lambda cancel, generation: gate.wait(1),
            lambda value: self.deliveries.append(value),
        )
        self.session.cancel()
        gate.set()
        self.drain()
        self.assertEqual(self.deliveries, [])
        self.assertEqual(self.session.result.text, "previous")
        self.assertEqual(self.session.phase, "idle")

    def test_shutdown_cleanup_failure_still_finishes(self):
        from types import SimpleNamespace
        from unittest.mock import Mock

        self.session.handsfree = SimpleNamespace(
            suspend=Mock(), join=Mock(side_effect=TimeoutError())
        )
        self.session.recognizer.close = Mock(
            side_effect=RuntimeError("fixture cleanup failure")
        )
        finished = threading.Event()
        self.session.close(finished.set)
        deadline = time.monotonic() + 2
        while not finished.is_set() and time.monotonic() < deadline:
            try:
                self.callbacks.get(timeout=0.05)()
            except queue.Empty:
                pass
        self.assertTrue(finished.is_set())
        self.assertTrue(self.session.closing)

    def test_old_operation_completion_cannot_override_new(self):
        ticket = self.session._begin("loading")
        self.session._run(
            ticket,
            lambda cancel, generation: "old",
            lambda result: self.deliveries.append(result),
        )
        self.session.operation += 1
        self.session.phase = "listening"
        self.drain()
        self.assertEqual(self.session.phase, "listening")
        self.assertEqual(self.deliveries, [])

    def test_failed_preferences_keep_active_configuration(self):
        original = self.session.preferences
        with patch.object(self.session.store, "save", side_effect=OSError("disk full")):
            self.session.save_preferences(replace(original, max_seconds=60), lambda: None)
            self.drain()
        self.assertIs(self.session.preferences, original)
        self.assertIn("disk full", self.session.notice)

    def test_no_second_operation_while_busy(self):
        self.session.phase = "loading"
        self.assertIsNone(self.session._begin("starting"))

    def test_remote_switch_unloads_without_removing_downloads(self):
        model = Path(self.tmp.name) / "weights"
        model.write_bytes(b"retained")
        with (
            patch.object(self.session, "scan_devices"),
            patch("dictadesk.core.session.remove_downloads") as remove,
        ):
            self.session.save_preferences(
                replace(self.session.preferences, backend="openai"), lambda: None
            )
            self.drain()
        self.assertFalse(self.session.recognizer.loaded)
        self.assertEqual(self.session.preferences.backend, "openai")
        self.assertEqual(model.read_bytes(), b"retained")
        remove.assert_not_called()

    def test_switch_cannot_interrupt_active_transcription(self):
        self.session.phase = "transcribing"
        self.session.save_preferences(
            replace(self.session.preferences, backend="openai"), lambda: None
        )
        self.assertTrue(self.session.recognizer.loaded)
        self.assertEqual(self.session.preferences.backend, "local")
        self.assertEqual(self.session.phase, "transcribing")

    def test_idle_unloads(self):
        self.session.last_used = time.monotonic() - 999
        self.session.tick()
        self.drain()
        self.assertFalse(self.session.recognizer.loaded)

    def test_zero_idle_timeout_keeps_model(self):
        self.session.preferences.idle_seconds = 0
        self.session.last_used = 0
        self.session.tick()
        self.assertTrue(self.session.recognizer.loaded)


if __name__ == "__main__":
    unittest.main()
