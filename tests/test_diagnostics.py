# SPDX-License-Identifier: GPL-3.0-only
"""Metadata diagnostics must be useful, bounded, private and nonblocking."""

from array import array
from pathlib import Path
import stat
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from dictadesk.audio.input import AudioLevels
from dictadesk.audio.service import Recognizer
from dictadesk.core.diagnostics import event, failure


class DiagnosticsTests(unittest.TestCase):
    def test_rotating_metadata_log_is_private_and_errors_do_not_echo_text(self):
        with (
            tempfile.TemporaryDirectory() as folder,
            patch.dict("os.environ", {"XDG_STATE_HOME": folder}),
        ):
            secret = "private speech and a credential"
            try:
                raise ValueError(secret)
            except ValueError as error:
                failure("test.failed", error)
            path = Path(folder) / "dictadesk/diagnostics.log"
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            contents = path.read_text()
            self.assertIn("ValueError", contents)
            self.assertNotIn(secret, contents)
            for index in range(3000):
                event("test.metadata", index=index, phase="a" * 300)
            self.assertLess(path.stat().st_size, 512 * 1024)
            files = list(path.parent.glob("diagnostics.log*"))
            self.assertLessEqual(len(files), 3)
            self.assertTrue(all(stat.S_IMODE(file.stat().st_mode) == 0o600 for file in files))

    def test_levels_distinguish_peak_meter_from_speech_gate(self):
        levels = AudioLevels(0.015)
        # A responding peak meter does not guarantee RMS reaches the gate.
        block = array("h", [300] * 1599 + [2000]).tobytes()
        peak, rms, samples = levels.observe(block)
        self.assertGreater(peak, 0.015)
        self.assertLess(rms, 0.015)
        self.assertEqual(samples, 1600)
        self.assertEqual(levels.voiced, 0)
        with patch("dictadesk.audio.input.event") as report:
            levels.report("test.levels")
        self.assertEqual(report.call_args.kwargs["samples"], 1600)
        self.assertEqual(report.call_args.kwargs["voiced_samples"], 0)
        self.assertNotIn("audio", report.call_args.kwargs)
        levels.last_block -= 6
        with self.assertRaisesRegex(RuntimeError, "stopped supplying audio"):
            levels.check_callbacks()

    def test_worker_diagnostics_exclude_vendor_stderr_and_transcription(self):
        with (
            tempfile.TemporaryDirectory() as folder,
            patch.dict("os.environ", {"XDG_STATE_HOME": folder}),
        ):
            script = """
import json, sys
sys.stdin.readline()
print('private vendor output', file=sys.stderr, flush=True)
print('DICTADESK_DIAGNOSTIC ' + json.dumps({'event': 'worker.failed', 'error_type': 'ValueError', 'text': 'private transcript', 'key': 'private key'}), file=sys.stderr, flush=True)
print(json.dumps({'text': 'private transcript'}), flush=True)
"""
            worker = Recognizer([sys.executable, "-u", "-c", script])
            try:
                self.assertEqual(
                    worker.request({"op": "transcribe"}, threading.Event())["text"],
                    "private transcript",
                )
                worker.close()
                # Collectors finish asynchronously; wait on the metadata with a bound.
                import time

                path = Path(folder) / "dictadesk/diagnostics.log"
                deadline = time.monotonic() + 1
                while "worker.failed" not in path.read_text() and time.monotonic() < deadline:
                    time.sleep(0.01)
                contents = path.read_text()
                self.assertIn('"error_type": "ValueError"', contents)
                self.assertIn('"characters": 18', contents)
                self.assertNotIn("private", contents)
            finally:
                worker.close()

    def test_unwritable_log_does_not_interrupt_operation(self):
        with patch("dictadesk.core.diagnostics.directory", side_effect=PermissionError()):
            event("test.no_storage", samples=1600)
