# SPDX-License-Identifier: GPL-3.0-only
"""Opt-in model download/reload tests in isolated caches; no microphone or API."""

import argparse
import json
from pathlib import Path
import threading

from dictadesk.audio.service import Recognizer
from dictadesk.core.models import variant_directory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download-models", action="store_true", required=True)
    args = parser.parse_args()
    assert args.download_models
    root = Path(__file__).resolve().parents[1] / ".qa"
    audio = root / "model-evaluation/jfk.wav"
    assert audio.is_file(), "Run model_evaluation.py --prepare first"
    for model, precision in (
        ("moonshine-tiny", "int8"),
        ("whisper-tiny", "int8"),
        ("whisper-tiny", "float32"),
    ):
        worker = Recognizer()
        payload = {
            "op": "load",
            "cache": str(root / "model-options"),
            "precision": precision,
            "local_model": model,
            "device": "cpu",
        }
        try:
            reply = worker.request(payload, threading.Event(), timeout=900)
            assert reply["loaded"] and reply["device"] == "cpu"
            result = worker.request({"op": "transcribe", "path": str(audio)}, threading.Event())
            assert "country" in result["text"].lower(), result
            print(model, precision, result["text"], flush=True)
            worker.close()
            # A new process must use the verified local copy, without downloads.
            worker.request({**payload, "local_only": True}, threading.Event())
            repeat = worker.request({"op": "transcribe", "path": str(audio)}, threading.Event())
            assert repeat["text"] == result["text"]
            parent = variant_directory(payload["cache"], precision, model)
            index = json.loads((parent / "current.json").read_text())
            assert index["files"]
            assert not any(p.is_symlink() for p in (parent / index["generation"]).rglob("*"))
            print("PASS downloaded and reloaded", model, precision, flush=True)
        finally:
            worker.close()


if __name__ == "__main__":
    main()
