# SPDX-License-Identifier: GPL-3.0-only
"""Private JSON protocol. Only this subprocess imports the inference runtime."""

import contextlib
import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
import uuid
import wave
from pathlib import Path

from ..core.diagnostics import event, failure


def configure_hub_downloads():
    from huggingface_hub.utils import get_session

    # GTK needs system site-packages, which can expose an older optional Brotli
    # module to the bundled HTTP client. Use its built-in compression decoders
    # instead; model downloads must not depend on the desktop's Brotli version.
    get_session().headers["Accept-Encoding"] = "gzip, deflate"


def transcribe(model, path, local_model):
    from ..core.models import LOCAL_MODELS

    with wave.open(path) as audio:
        event(
            "worker.audio",
            samples=audio.getnframes(),
            rate=audio.getframerate(),
            local_model=local_model,
        )

    limit = LOCAL_MODELS[local_model].get("max_audio_seconds")
    if limit is None:
        return model.recognize(path)
    # Whisper's preprocessor truncates at 30 seconds. Ordinary mouse recordings
    # can be longer than the short chunks used by either keyboard mode.
    with wave.open(path) as audio:
        if audio.getnframes() <= limit * audio.getframerate():
            return model.recognize(path)
        if (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) != (16000, 1, 2):
            raise ValueError("The local model requires mono 16 kHz PCM16 recording audio.")
        import numpy as np
        from ..core.transcript import continuation

        result = ""
        while data := audio.readframes(25 * 16000):
            samples = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768
            text = model.recognize(samples, sample_rate=16000).strip()
            result += continuation(result, text)
        return result


def transcribe_pcm(model, encoded):
    import base64
    import numpy as np

    maximum = 8 * 16000 * 2
    if not isinstance(encoded, str) or len(encoded) > 4 * ((maximum + 2) // 3):
        raise ValueError("Command audio exceeds the in-memory limit.")
    data = base64.b64decode(encoded, validate=True)
    if not data or len(data) > maximum or len(data) % 2:
        raise ValueError("Invalid command audio.")
    samples = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768
    event(
        "worker.pcm",
        samples=len(samples),
        rate=16000,
        peak=float(np.max(np.abs(samples))),
        rms=float(np.sqrt(np.mean(samples**2))),
    )
    return model.recognize(samples, sample_rate=16000)


def cloud(request):
    from ..core.models import REMOTE_MODELS

    if request["model"] not in REMOTE_MODELS:
        raise ValueError("Choose a supported remote transcription model in Preferences.")
    audio = Path(request["path"]).read_bytes()
    if len(audio) > 24 * 1024 * 1024:
        raise ValueError("Recording exceeds the cloud upload limit. Use a shorter recording.")
    boundary = uuid.uuid4().hex
    fields = {"model": request["model"], "response_format": "json"}
    if request.get("language"):
        # The current API expects an array, encoded as repeated multipart fields.
        # Keep the existing single-language preference as one array element.
        fields["languages[]"] = request["language"]
    parts = []
    for key, value in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode()
        )
    parts.extend(
        [
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="dictation.wav"\r\nContent-Type: audio/wav\r\n\r\n'.encode(),
            audio,
            f"\r\n--{boundary}--\r\n".encode(),
        ]
    )
    req = urllib.request.Request(
        "https://api.openai.com/v1/audio/transcriptions",
        b"".join(parts),
        headers={
            "Authorization": "Bearer " + request["key"],
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as response:
            data = json.load(response)
        if not isinstance(data, dict) or not isinstance(data.get("text"), str):
            raise ValueError("Invalid transcription response")
        # Detected-language metadata can be absent or empty; only text is needed.
        return data["text"]
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"OpenAI rejected the transcription (HTTP {exc.code}). Check your key, account and model."
        ) from None
    except (ValueError, UnicodeError):
        raise RuntimeError("OpenAI returned an invalid transcript. Try again.") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise RuntimeError(
            "Could not reach OpenAI. Check your connection and try again."
        ) from None


def load_model(request):
    import hashlib
    from ..core.settings import private_write
    from ..core.models import LOCAL_MODELS, variant_directory

    model_id = request.get("local_model", "parakeet")
    root = variant_directory(request["cache"], request["precision"], model_id)
    if root.is_symlink():
        raise ValueError("The model directory must not be a symbolic link.")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    current = root / "current.json"
    specification = LOCAL_MODELS[model_id]
    quantization = specification.get("quantization", {"int8": "int8", "float32": None})[
        request["precision"]
    ]
    provider = (
        "CUDAExecutionProvider" if request.get("device") == "cuda" else "CPUExecutionProvider"
    )
    if provider == "CUDAExecutionProvider":
        import onnxruntime

        if provider not in onnxruntime.get_available_providers():
            raise ValueError(
                "CUDA is unavailable in this installation. Choose CPU in Preferences."
            )

    def manifest(folder):
        hashes = {}
        for child in sorted(folder.rglob("*")):
            if child.is_symlink():
                raise ValueError("Model files must not be symbolic links. Use Repair Model.")
            if child.is_file() and ".cache" not in child.relative_to(folder).parts:
                with child.open("rb") as stream:
                    hashes[str(child.relative_to(folder))] = hashlib.file_digest(
                        stream, "sha256"
                    ).hexdigest()
        return hashes

    def load(folder, *, allow_download=False):
        if specification.get("engine") == "moonshine":
            from .moonshine import load as load_moonshine

            return load_moonshine(folder, allow_download=allow_download)
        import onnx_asr

        return onnx_asr.load_model(
            specification["runtime"],
            str(folder),
            quantization=quantization,
            providers=[provider],
        )

    if current.exists() and not request.get("repair"):
        saved = json.loads(current.read_text())
        generation = saved["generation"]
        if (
            not isinstance(generation, str)
            or len(generation) != 32
            or any(c not in "0123456789abcdef" for c in generation)
        ):
            raise ValueError("The model index is damaged. Use Repair Model.")
        folder = root / generation
        if folder.is_symlink() or not folder.is_dir() or manifest(folder) != saved["files"]:
            raise ValueError("The model files are incomplete or changed. Use Repair Model.")
        return load(folder)
    if request.get("local_only"):
        raise ValueError("The downloaded model is unavailable. Download it in Preferences.")
    if specification.get("engine") != "moonshine":
        configure_hub_downloads()
    # Publish only the pointer atomically. Cancellation cannot remove the old model.
    # Retired generations remain available as backups; no existing cache is deleted.
    with tempfile.TemporaryDirectory(prefix=".download-", dir=root) as staging:
        stage = Path(staging) / "model"
        model = load(stage, allow_download=True)
        hashes = manifest(stage)
        if not hashes:
            raise ValueError("The model download did not produce any files.")
        generation = uuid.uuid4().hex
        stage.rename(root / generation)
        private_write(current, json.dumps({"generation": generation, "files": hashes}).encode())
        return model


def main():
    model = None
    model_location = None
    event("worker.ready")
    for line in sys.stdin:
        try:
            request = json.loads(line)
            with contextlib.redirect_stdout(sys.stderr):
                if request["op"] == "load":
                    from .compute import effective_device

                    device, compute_notice = effective_device(request)
                    model = load_model({**request, "device": device})
                    model_location = (
                        request["cache"],
                        request["precision"],
                        request.get("local_model", "parakeet"),
                    )
                    from ..core.models import note_use

                    note_use(*model_location)
                    result = {
                        "loaded": True,
                        "device": device,
                        "compute_notice": compute_notice,
                    }
                elif request["op"] == "capabilities":
                    from .compute import cuda_capabilities

                    result = cuda_capabilities()
                elif request["op"] == "cloud":
                    result = {"text": cloud(request)}
                elif request["op"] == "transcribe_pcm":
                    if model is None:
                        raise ValueError("Load the local model first.")
                    result = {"text": transcribe_pcm(model, request["audio"])}
                elif request["op"] == "transcribe":
                    if model is None:
                        raise ValueError("Load the local model first.")
                    result = {"text": transcribe(model, request["path"], model_location[2])}
                    note_use(*model_location)
                else:
                    raise ValueError("Unsupported worker operation.")
        except Exception as exc:
            failure("worker.failed", exc)
            # Never echo a request or credential; third-party errors may contain URLs.
            message = (
                str(exc)
                if isinstance(exc, (ValueError, RuntimeError))
                else "Speech processing failed. Check the model, microphone or network and retry."
            )
            result = {"error": message[:600]}
        print(json.dumps(result), flush=True)
        request = None


if __name__ == "__main__":
    os.umask(0o077)
    main()
