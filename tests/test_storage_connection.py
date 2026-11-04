# SPDX-License-Identifier: GPL-3.0-only
import io
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
import urllib.error
from unittest.mock import patch

from dictadesk.core import models
from dictadesk.core.connection import test_connection


class CacheTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name)
        self.root = self.home / "models"
        self.root.mkdir(mode=0o700)
        mocked = patch.object(models, "model_directory", return_value=self.root)
        mocked.start()
        self.addCleanup(mocked.stop)

    def pointer(self, precision="int8", age=40):
        folder = self.root / precision
        folder.mkdir(exist_ok=True)
        path = folder / "current.json"
        path.write_text("{}")
        when = time.time() - age * 86400
        os.utime(path, (when, when))
        return folder

    def test_old_download_suggested_but_not_deleted(self):
        folder = self.pointer()
        info = models.cache_info()
        self.assertEqual(info["unused_days"], 40)
        self.assertTrue(info["exists"])
        self.assertTrue((folder / "current.json").exists())

    def test_successful_use_resets_age_across_restarts(self):
        self.pointer()
        models.note_use(self.root, "int8")
        self.assertEqual(models.cache_info()["unused_days"], 0)

    def test_recent_download_prevents_remove_all_suggestion(self):
        self.pointer()
        self.pointer("float32", age=1)
        self.assertEqual(models.cache_info()["unused_days"], 1)

    def test_corrupt_future_or_nan_usage_does_not_claim_stale(self):
        folder = self.pointer()
        for value in ("broken", "nan", "inf", str(time.time() + 1000)):
            with self.subTest(value=value):
                (folder / "last-used").write_text(value)
                self.assertIsNone(models.cache_info()["unused_days"])

    def test_usage_counts_all_generations_not_link_targets(self):
        file = self.root / "weights"
        file.write_bytes(b"model" * 2000)
        (self.root / "duplicate-hardlink").hardlink_to(file)
        outside = self.home / "exported-transcript"
        outside.write_text("keep this")
        (self.root / "link").symlink_to(outside)
        self.assertEqual(models.cache_info()["bytes"], file.stat().st_blocks * 512)
        models.remove_downloads()
        self.assertEqual(outside.read_text(), "keep this")
        self.assertFalse(self.root.exists())

    def test_symlink_cache_refused(self):
        self.root.rmdir()
        self.root.symlink_to(self.home, target_is_directory=True)
        with self.assertRaises(ValueError):
            models.remove_downloads()

    def test_remove_one_model_preserves_other_and_tracks_both(self):
        parakeet = self.pointer()
        whisper = self.root / "whisper-base/int8"
        whisper.mkdir(parents=True)
        (whisper / "current.json").write_text("{}")
        models.note_use(self.root, "int8", "whisper-base")
        self.assertEqual(models.cache_info()["unused_days"], 0)
        self.assertTrue(models.downloaded("int8", "whisper-base"))
        models.remove_downloads("whisper-base")
        self.assertTrue(parakeet.is_dir())
        self.assertFalse(whisper.exists())
        whisper.mkdir()
        (whisper / "current.json").write_text("{}")
        models.remove_downloads("parakeet")
        self.assertTrue(whisper.is_dir())
        self.assertFalse(parakeet.exists())


class InstalledCacheTests(unittest.TestCase):
    def test_missing_package_cache_fails_without_home_fallback(self):
        with tempfile.TemporaryDirectory() as folder:
            with (
                patch.object(models, "packaged", return_value=True),
                patch.object(models, "SYSTEM_CACHE", Path(folder) / "absent"),
            ):
                with self.assertRaises(FileNotFoundError):
                    models.model_directory(create=True)
                self.assertFalse((Path(folder) / "absent").exists())

    def test_wrong_root_owner_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            if os.getuid() == 0:
                self.skipTest("Requires ordinary desktop user ownership")
            with (
                patch.object(models, "packaged", return_value=True),
                patch.object(models, "SYSTEM_CACHE", Path(folder)),
            ):
                with self.assertRaises(ValueError):
                    models.model_directory(create=True)


class RemovalScriptTests(unittest.TestCase):
    def test_remove_purge_clean_only_models_upgrade_keeps_them(self):
        script = Path(__file__).resolve().parents[1] / "packaging/postrm"
        for operation in ("upgrade", "remove", "purge"):
            with self.subTest(operation=operation), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                cache = root / "var/cache/dictadesk/1000"
                cache.mkdir(parents=True)
                (cache / "weights").write_bytes(b"weights")
                external = root / "home/user"
                external.mkdir(parents=True)
                for name in ("settings", "keyring", "exported-transcript"):
                    (external / name).write_text("preserve")
                (cache / "link").symlink_to(external, target_is_directory=True)
                subprocess.run(
                    ["sh", str(script), operation],
                    env={**os.environ, "DPKG_ROOT": str(root)},
                    check=True,
                )
                self.assertEqual(cache.exists(), operation == "upgrade")
                self.assertEqual(len(list(external.iterdir())), 3)


class ConnectionTests(unittest.TestCase):
    def test_retired_and_streaming_models_never_contact_file_service(self):
        for model in (
            "whisper-1",
            "gpt-4o-transcribe",
            "gpt-4o-mini-transcribe",
            "gpt-live-transcribe",
        ):
            with self.subTest(model=model), patch("urllib.request.urlopen") as request:
                self.assertIn("supported service model", test_connection("fixture-key", model))
                request.assert_not_called()

    def test_metadata_get_with_no_audio_or_transcription(self):
        with patch(
            "urllib.request.urlopen", return_value=io.BytesIO(b'{"id":"gpt-transcribe"}')
        ) as open_url:
            message = test_connection("fixture-key", "gpt-transcribe")
        request = open_url.call_args.args[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertIsNone(request.data)
        self.assertEqual(request.full_url, "https://api.openai.com/v1/models/gpt-transcribe")
        self.assertIn("No audio was sent", message)
        self.assertIn("not tested", message)

    def test_missing_key_does_not_connect(self):
        with patch("urllib.request.urlopen") as request:
            self.assertIn("Enter an API key", test_connection("", "gpt-transcribe"))
            request.assert_not_called()

    def test_http_errors_and_network_failures_do_not_leak_secrets(self):
        for code in (401, 403, 404, 429, 500):
            error = urllib.error.HTTPError(
                "https://example.invalid", code, "secret-key", {}, None
            )
            with patch("urllib.request.urlopen", side_effect=error):
                message = test_connection("secret-key", "gpt-transcribe")
            self.assertNotIn("secret-key", message)
        with patch("urllib.request.urlopen", side_effect=TimeoutError("secret-key")):
            self.assertIn("Could not connect", test_connection("secret-key", "gpt-transcribe"))
