# SPDX-License-Identifier: GPL-3.0-only
"""Real ONNX coverage for Hugging Face snapshot symlinks and external weights."""

import json
from pathlib import Path
import shutil
import struct
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import onnxruntime as ort

from dictadesk.audio.worker import load_model

# Original fixture: Add(x, bias), float32[1], IR 8/opset 13. The initializer
# stores its one float in encoder-model.onnx.data, not inside the ONNX protobuf.
# No onnx authoring package, downloaded model, or network is required.
MODEL = bytes.fromhex(
    "08083a770a110a01780a04626961731201792203416464120d65787465726e616c2d74657374"
    "2a31080110014204626961736a230a086c6f636174696f6e1217656e636f6465722d6d6f6465"
    "6c2e6f6e6e782e6461746170015a0f0a0178120a0a08080112040a020801620f0a0179120a"
    "0a08080112040a0208014202100d"
)


class ExternalWeightsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.snapshot = self.root / "hub/snapshots/revision"
        self.snapshot.mkdir(parents=True)
        for name, directory, data in (
            ("encoder-model.onnx", "9a", MODEL),
            ("encoder-model.onnx.data", "79", struct.pack("<f", 2)),
        ):
            blob = self.root / "hub/blobs" / directory / "content"
            blob.parent.mkdir(parents=True)
            blob.write_bytes(data)
            (self.snapshot / name).symlink_to(blob)
        self.request = {"cache": str(self.root / "dictadesk"), "precision": "float32"}

    def open_model(self, folder):
        return ort.InferenceSession(
            str(folder / "encoder-model.onnx"), providers=["CPUExecutionProvider"]
        )

    def assert_inference(self, model):
        np.testing.assert_array_equal(
            model.run(None, {"x": np.array([3], dtype=np.float32)})[0], [5]
        )

    def materialized_download(self, name, path, **kwargs):
        folder = Path(path)
        if not folder.exists():
            # HF local_dir downloads use regular files. Reproduce that boundary
            # with copies from our content-addressed fixture, not snapshot links.
            shutil.copytree(self.snapshot, folder, symlinks=False)
        return self.open_model(folder)

    def test_split_cache_symlinks_reproduce_reported_path_error(self):
        with self.assertRaisesRegex(Exception, "External data path escapes model directory"):
            self.open_model(self.snapshot)

    def test_local_download_and_verified_reload_keep_external_weights_together(self):
        with patch("onnx_asr.load_model", side_effect=self.materialized_download):
            self.assert_inference(load_model(self.request))
            self.assert_inference(load_model({**self.request, "local_only": True}))
        parent = self.root / "dictadesk/float32"
        index = json.loads((parent / "current.json").read_text())
        self.assertEqual(set(index["files"]), {"encoder-model.onnx", "encoder-model.onnx.data"})
        self.assertFalse(any(p.is_symlink() for p in (parent / index["generation"]).iterdir()))
        # Both the model and separate weights participate in integrity checks.
        weights = parent / index["generation"] / "encoder-model.onnx.data"
        weights.write_bytes(struct.pack("<f", 7))
        with patch("onnx_asr.load_model") as loader:
            with self.assertRaisesRegex(ValueError, "incomplete or changed"):
                load_model({**self.request, "local_only": True})
            loader.assert_not_called()

    def test_missing_external_weights_rejected_before_reload(self):
        with patch("onnx_asr.load_model", side_effect=self.materialized_download):
            load_model(self.request)
        parent = self.root / "dictadesk/float32"
        index = json.loads((parent / "current.json").read_text())
        (parent / index["generation"] / "encoder-model.onnx.data").unlink()
        with patch("onnx_asr.load_model") as loader:
            with self.assertRaisesRegex(ValueError, "incomplete or changed"):
                load_model({**self.request, "local_only": True})
            loader.assert_not_called()
