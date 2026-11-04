# SPDX-License-Identifier: GPL-3.0-only
import types
import unittest
from unittest.mock import Mock, patch

from dictadesk.audio.compute import cuda_capabilities, effective_device


class ComputeTests(unittest.TestCase):
    def runtime(self, providers):
        return types.SimpleNamespace(
            get_available_providers=Mock(return_value=providers),
            SessionOptions=Mock(),
            GraphOptimizationLevel=types.SimpleNamespace(ORT_DISABLE_ALL=0),
            InferenceSession=Mock(),
        )

    def test_cpu_selection_never_probes_cuda(self):
        with patch("dictadesk.audio.compute.cuda_capabilities") as probe:
            self.assertEqual(effective_device({"device": "cpu"}), ("cpu", ""))
            probe.assert_not_called()

    def test_cpu_runtime_falls_back_before_attempting_cuda_initialization(self):
        runtime = self.runtime(["CPUExecutionProvider"])
        with patch.dict("sys.modules", {"onnxruntime": runtime}):
            device, notice = effective_device({"device": "cuda"})
        self.assertEqual(device, "cpu")
        self.assertIn("Switched to CPU", notice)
        runtime.InferenceSession.assert_not_called()

    def test_advertised_provider_is_not_enough(self):
        runtime = self.runtime(["CPUExecutionProvider", "CUDAExecutionProvider"])
        runtime.InferenceSession.side_effect = RuntimeError("missing driver")
        with patch.dict("sys.modules", {"onnxruntime": runtime}):
            self.assertFalse(cuda_capabilities()["cuda"])

    def test_silent_cpu_fallback_is_unavailable(self):
        runtime = self.runtime(["CPUExecutionProvider", "CUDAExecutionProvider"])
        runtime.InferenceSession.return_value.get_providers.return_value = [
            "CPUExecutionProvider"
        ]
        with patch.dict("sys.modules", {"onnxruntime": runtime}):
            self.assertFalse(cuda_capabilities()["cuda"])

    def test_successful_cuda_execution_keeps_cuda(self):
        runtime = self.runtime(["CPUExecutionProvider", "CUDAExecutionProvider"])
        runtime.InferenceSession.return_value.get_providers.return_value = [
            "CUDAExecutionProvider"
        ]
        runtime.InferenceSession.return_value.run.return_value = [[2.0]]
        with patch.dict("sys.modules", {"onnxruntime": runtime}):
            self.assertEqual(effective_device({"device": "cuda"}), ("cuda", ""))
        runtime.SessionOptions.return_value.add_session_config_entry.assert_called_with(
            "session.disable_cpu_ep_fallback", "1"
        )
        runtime.InferenceSession.return_value.run.assert_called_once()
