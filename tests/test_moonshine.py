# SPDX-License-Identifier: GPL-3.0-only
import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from dictadesk.audio.compute import effective_device
from dictadesk.audio.worker import load_model
from dictadesk.core.models import remove_downloads, cache_info
from dictadesk.core.settings import Preferences, SettingsStore


class MoonshineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.request = {
            "cache": str(self.root),
            "precision": "int8",
            "local_model": "moonshine-tiny",
        }
        self.files = {
            name: hashlib.sha256(name.encode()).hexdigest()
            for name in ("encoder_model.ort", "decoder_model_merged.ort", "tokenizer.bin")
        }

    def download(self, url, **kwargs):
        return io.BytesIO(url.full_url.rsplit("/", 1)[1].encode())

    def install(self):
        with (
            patch("dictadesk.audio.moonshine.FILES", self.files),
            patch("dictadesk.audio.moonshine.urllib.request.urlopen", self.download),
            patch("dictadesk.audio.moonshine.Moonshine", return_value=Mock()),
        ):
            load_model(self.request)

    def test_verified_download_then_offline_reload(self):
        self.install()
        with (
            patch("dictadesk.audio.moonshine.urllib.request.urlopen") as network,
            patch("dictadesk.audio.moonshine.Moonshine") as runtime,
        ):
            load_model({**self.request, "local_only": True})
            runtime.assert_called_once()
            network.assert_not_called()

    def test_bad_download_hash_never_replaces_working_generation(self):
        self.install()
        pointer = self.root / "moonshine-tiny/int8/current.json"
        previous = pointer.read_bytes()
        with (
            patch(
                "dictadesk.audio.moonshine.urllib.request.urlopen",
                return_value=io.BytesIO(b"corrupt"),
            ),
            patch("dictadesk.audio.moonshine.Moonshine") as runtime,
        ):
            with self.assertRaisesRegex(ValueError, "verification failed"):
                load_model({**self.request, "repair": True})
            runtime.assert_not_called()
        self.assertEqual(pointer.read_bytes(), previous)
        self.assertFalse(list(pointer.parent.glob(".download-*")))

    def test_interrupted_download_preserves_previous_generation(self):
        self.install()
        pointer = self.root / "moonshine-tiny/int8/current.json"
        previous = pointer.read_bytes()
        with patch(
            "dictadesk.audio.moonshine.urllib.request.urlopen", side_effect=InterruptedError
        ):
            with self.assertRaises(InterruptedError):
                load_model({**self.request, "repair": True})
        self.assertEqual(pointer.read_bytes(), previous)

    def test_removal_and_storage_do_not_touch_other_models(self):
        self.install()
        other = self.root / "int8"
        other.mkdir()
        sentinel = other / "keep"
        sentinel.write_bytes(b"parakeet")
        with patch("dictadesk.core.models.model_directory", return_value=self.root):
            self.assertGreater(cache_info()["bytes"], 0)
            remove_downloads("moonshine-tiny")
        self.assertEqual(sentinel.read_bytes(), b"parakeet")
        self.assertFalse((self.root / "moonshine-tiny/int8").exists())

    def test_choices_persist_without_changing_the_default(self):
        store = SettingsStore(self.root / "settings.json")
        self.assertEqual(Preferences().local_model, "parakeet")
        for model in ("moonshine-tiny", "whisper-tiny"):
            store.save(Preferences(local_model=model))
            self.assertEqual(store.read().local_model, model)
        with self.assertRaisesRegex(ValueError, "Int8 precision only"):
            Preferences(local_model="moonshine-tiny", quantization="float32").validate()

    def test_saved_cuda_choice_uses_cpu_for_moonshine(self):
        with patch("dictadesk.audio.compute.cuda_capabilities") as probe:
            device, notice = effective_device(
                {"local_model": "moonshine-tiny", "device": "cuda"}
            )
        self.assertEqual(device, "cpu")
        self.assertIn("Moonshine", notice)
        probe.assert_not_called()
