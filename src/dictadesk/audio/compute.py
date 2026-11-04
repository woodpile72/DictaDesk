# SPDX-License-Identifier: GPL-3.0-only
"""Compute checks run in the speech subprocess, never the GTK process."""

# Self-contained ONNX Add(x, x), float32[1], IR 8 / opset 13.
# No downloaded model, microphone, or network is needed for this check.
PROBE_MODEL = bytes.fromhex(
    "08083a3e0a0e0a01780a01781201792203416464120a637564612d636865636b5a0f0a0178120a0a08080112040a020801620f0a0179120a0a08080112040a0208014202100d"
)


def cuda_capabilities():
    import onnxruntime as ort

    if "CUDAExecutionProvider" not in ort.get_available_providers():
        return {"cuda": False, "reason": "This installation includes the CPU runtime only."}
    try:
        import numpy as np

        options = ort.SessionOptions()
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_DISABLE_ALL
        options.add_session_config_entry("session.disable_cpu_ep_fallback", "1")
        probe = ort.InferenceSession(
            PROBE_MODEL, sess_options=options, providers=["CUDAExecutionProvider"]
        )
        probe.disable_fallback()
        if "CUDAExecutionProvider" not in probe.get_providers():
            raise RuntimeError("CUDA initialization fell back to CPU")
        output = probe.run(None, {"x": np.array([1], dtype=np.float32)})
        if float(output[0][0]) != 2:
            raise RuntimeError("CUDA calculation failed")
    except Exception:
        return {
            "cuda": False,
            "reason": "CUDA could not initialize with this runtime, driver and GPU.",
        }
    return {"cuda": True, "reason": "CUDA is available."}


def effective_device(request):
    if request.get("device") != "cuda":
        return "cpu", ""
    if request.get("local_model") == "moonshine-tiny":
        return "cpu", "Moonshine Tiny uses CPU. Switched to CPU."
    capabilities = cuda_capabilities()
    if not capabilities["cuda"]:
        return "cpu", capabilities["reason"] + " Switched to CPU."
    return "cuda", ""
