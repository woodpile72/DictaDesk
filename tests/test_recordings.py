# SPDX-License-Identifier: GPL-3.0-only
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from dictadesk.core.recordings import cleanup_abandoned, workspace


class RecordingCleanupTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / "recordings"

    def crash(self):
        subprocess.run(
            [
                sys.executable,
                "-c",
                """
import os, sys
from dictadesk.core.recordings import workspace
with workspace(sys.argv[1]) as path:
    (path / 'recording.wav').write_bytes(b'private audio')
    (path / 'preview.wav').write_bytes(b'private preview')
    os._exit(0)
""",
                str(self.root),
            ],
            check=True,
        )

    def test_abrupt_exit_releases_lease_and_next_launch_cleans_audio(self):
        self.crash()
        self.assertEqual(cleanup_abandoned(self.root), 1)
        self.assertEqual(list(self.root.glob("session-*")), [])
        self.assertEqual(cleanup_abandoned(self.root), 0)

    def test_live_workspace_survives_cleanup_from_another_process(self):
        with workspace(self.root) as path:
            audio = path / "recording.wav"
            audio.write_bytes(b"active")
            subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "import sys; from dictadesk.core.recordings import cleanup_abandoned; assert cleanup_abandoned(sys.argv[1]) == 0",
                    str(self.root),
                ],
                check=True,
            )
            self.assertEqual(audio.read_bytes(), b"active")
        self.assertFalse(path.exists())

    def test_cleanup_does_not_touch_exports_unknown_folders_or_symlink_targets(self):
        self.crash()
        outside = self.root.parent / "export.txt"
        outside.write_text("keep")
        old = next(self.root.glob("session-*"))
        (old / "link").symlink_to(outside)
        unknown = self.root / "my-recording.wav"
        unknown.write_text("keep")
        alias = self.root / ("session-" + "a" * 32)
        alias.symlink_to(self.root.parent, target_is_directory=True)
        self.assertEqual(cleanup_abandoned(self.root), 1)
        self.assertTrue(alias.is_symlink())
        self.assertEqual(unknown.read_text(), "keep")
        self.assertEqual(outside.read_text(), "keep")

    def test_cancelled_or_failed_operation_cleans_workspace(self):
        with self.assertRaises(InterruptedError):
            with workspace(self.root) as path:
                (path / "recording.wav").write_bytes(b"private")
                raise InterruptedError()
        self.assertFalse(path.exists())

    def test_shared_or_symlink_directory_refused(self):
        self.root.mkdir(mode=0o755)
        with self.assertRaises(ValueError):
            cleanup_abandoned(self.root)
        self.root.rmdir()
        self.root.symlink_to(self.root.parent, target_is_directory=True)
        with self.assertRaises(ValueError):
            cleanup_abandoned(self.root)
