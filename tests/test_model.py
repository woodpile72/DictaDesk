# SPDX-License-Identifier: GPL-3.0-only
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch
from unittest.mock import Mock
import wave
from dictadesk.audio.worker import load_model, transcribe


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.request = {"cache": str(self.root), "precision": "int8"}
        self.fake = types.SimpleNamespace(load_model=self.create_model)
        self.mock = patch.dict("sys.modules", {"onnx_asr": self.fake})
        self.mock.start()
        self.addCleanup(self.mock.stop)

    def create_model(self, name, path, **kwargs):
        folder = Path(path)
        folder.mkdir(exist_ok=True, parents=True)
        (folder / "model.onnx").write_bytes(b"fixture model")
        return object()

    def current(self):
        return self.root / "int8/current.json"

    def test_fresh_load_publishes_complete_generation(self):
        load_model(self.request)
        data = json.loads(self.current().read_text())
        self.assertIn("model.onnx", data["files"])
        self.assertTrue((self.root / "int8" / data["generation"] / "model.onnx").exists())

    def test_startup_missing_model_never_downloads(self):
        with patch.object(self.fake, "load_model") as loader:
            with self.assertRaisesRegex(ValueError, "unavailable"):
                load_model({**self.request, "local_only": True})
            loader.assert_not_called()

    def test_startup_loads_existing_verified_model(self):
        load_model(self.request)
        pointer = self.current().read_bytes()
        self.assertIsNotNone(load_model({**self.request, "local_only": True}))
        self.assertEqual(self.current().read_bytes(), pointer)

    def test_repair_failure_preserves_current(self):
        load_model(self.request)
        original = self.current().read_bytes()
        self.fake.load_model = lambda *a, **kw: (_ for _ in ()).throw(
            RuntimeError("download failure")
        )
        with self.assertRaises(RuntimeError):
            load_model({**self.request, "repair": True})
        self.assertEqual(self.current().read_bytes(), original)

    def test_pointer_save_failure_preserves_current(self):
        load_model(self.request)
        original = self.current().read_bytes()
        with patch("dictadesk.core.settings.private_write", side_effect=OSError("full")):
            with self.assertRaises(OSError):
                load_model({**self.request, "repair": True})
        self.assertEqual(self.current().read_bytes(), original)

    def test_changed_model_is_rejected(self):
        load_model(self.request)
        generation = json.loads(self.current().read_text())["generation"]
        (self.root / "int8" / generation / "model.onnx").write_bytes(b"damaged")
        with self.assertRaisesRegex(ValueError, "changed"):
            load_model(self.request)

    def test_model_path_escape_rejected(self):
        self.current().parent.mkdir()
        self.current().write_text(json.dumps({"generation": "../../outside", "files": {}}))
        with self.assertRaises(ValueError):
            load_model(self.request)

    def test_model_symlinks_rejected(self):
        load_model(self.request)
        generation = json.loads(self.current().read_text())["generation"]
        folder = self.root / "int8" / generation
        (folder / "link").symlink_to("/etc/hosts")
        with self.assertRaisesRegex(ValueError, "symbolic"):
            load_model(self.request)

    def test_repair_keeps_previous_generation(self):
        load_model(self.request)
        generation = json.loads(self.current().read_text())["generation"]
        load_model({**self.request, "repair": True})
        self.assertTrue((self.root / "int8" / generation).is_dir())
        self.assertNotEqual(json.loads(self.current().read_text())["generation"], generation)

    def test_whisper_uses_separate_cache_and_runtime(self):
        load_model(self.request)
        parakeet = self.current().read_bytes()
        with patch.object(self.fake, "load_model", side_effect=self.create_model) as loader:
            load_model({**self.request, "local_model": "whisper-base"})
        self.assertEqual(loader.call_args.args[0], "whisper-base")
        self.assertEqual(loader.call_args.kwargs["quantization"], "int8")
        self.assertTrue((self.root / "whisper-base/int8/current.json").is_file())
        self.assertEqual(self.current().read_bytes(), parakeet)

    def test_whisper_tiny_maps_int8_to_hugging_face_export_name(self):
        with patch.object(self.fake, "load_model", side_effect=self.create_model) as loader:
            load_model({**self.request, "local_model": "whisper-tiny"})
        self.assertEqual(loader.call_args.args[0], "onnx-community/whisper-tiny")
        self.assertEqual(loader.call_args.kwargs["quantization"], "quantized")
        self.assertTrue((self.root / "whisper-tiny/int8/current.json").is_file())

    def test_moonshine_rejects_unavailable_precision_before_loading(self):
        with self.assertRaisesRegex(ValueError, "supported local model and precision"):
            load_model(
                {**self.request, "local_model": "moonshine-tiny", "precision": "float32"}
            )

    def test_missing_whisper_never_falls_back_to_parakeet(self):
        load_model(self.request)
        with patch.object(self.fake, "load_model") as loader:
            with self.assertRaisesRegex(ValueError, "unavailable"):
                load_model({**self.request, "local_model": "whisper-base", "local_only": True})
            loader.assert_not_called()

    def test_unknown_model_is_rejected_before_loading(self):
        with patch.object(self.fake, "load_model") as loader:
            with self.assertRaises(ValueError):
                load_model({**self.request, "local_model": "../../elsewhere"})
            loader.assert_not_called()

    def test_long_whisper_audio_is_not_silently_truncated(self):
        path = self.root / "long.wav"
        frames = 61 * 16000
        with wave.open(str(path), "wb") as audio:
            audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            audio.writeframes(bytes(frames * 2))
        for model in ("whisper-base", "whisper-tiny", "moonshine-tiny"):
            with self.subTest(model=model):
                recognizer = Mock()
                recognizer.recognize.side_effect = ["First.", "Second.", "Tail."]
                self.assertEqual(
                    transcribe(recognizer, str(path), model), "First. Second. Tail."
                )
                lengths = [len(call.args[0]) for call in recognizer.recognize.call_args_list]
                self.assertEqual(lengths, [25 * 16000, 25 * 16000, 11 * 16000])
                self.assertEqual(sum(lengths), frames)
