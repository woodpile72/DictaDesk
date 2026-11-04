# SPDX-License-Identifier: GPL-3.0-only
"""App-owned models: removable by the user, and by package removal when installed."""

import os
from pathlib import Path
import shutil
import stat
import time
from .settings import directory

SYSTEM_CACHE = Path("/var/cache/dictadesk")
UNUSED_DAYS = 30
DEFAULT_REMOTE_MODEL = "gpt-transcribe"
REMOTE_MODELS = {DEFAULT_REMOTE_MODEL: "GPT Transcribe"}
LOCAL_MODELS = {
    "parakeet": {
        "name": "Parakeet TDT 0.6B v3",
        "runtime": "nemo-parakeet-tdt-0.6b-v3",
        "sizes": {"int8": "640 MiB", "float32": "2.4 GiB"},
        "description": "Recommended default. Best accuracy in our English tests; uses more memory and disk space.",
    },
    "moonshine-tiny": {
        "name": "Moonshine Tiny (English)",
        "engine": "moonshine",
        "sizes": {"int8": "42 MiB"},
        "description": "English only. Fast and light on memory; may miss more words than Parakeet. Uses CPU.",
        "max_audio_seconds": 25,
    },
    "whisper-base": {
        "name": "Whisper Base",
        "runtime": "whisper-base",
        "sizes": {"int8": "104 MiB", "float32": "381 MiB"},
        "description": "Multilingual alternative with a smaller download. Less accurate than Parakeet in our English tests.",
        "max_audio_seconds": 30,
    },
    "whisper-tiny": {
        "name": "Whisper Tiny",
        "runtime": "onnx-community/whisper-tiny",
        "quantization": {"int8": "quantized", "float32": None},
        "sizes": {"int8": "40 MiB", "float32": "146 MiB"},
        "description": "Small multilingual download. Least accurate in our tests; may omit or repeat text. Small download does not guarantee low memory use.",
        "max_audio_seconds": 30,
    },
}


def variant_directory(root, precision, model="parakeet"):
    if model not in LOCAL_MODELS or precision not in LOCAL_MODELS[model]["sizes"]:
        raise ValueError("Choose a supported local model and precision.")
    root = Path(root)
    # Retain existing Parakeet downloads in their original locations.
    if model != "parakeet":
        root = root / model
        if root.is_symlink():
            raise ValueError("The model directory must not be a symbolic link.")
    return root / precision


def note_use(root, precision, model="parakeet"):
    """A successful load or local transcription counts as model activity."""
    from .settings import private_write

    try:
        private_write(
            variant_directory(root, precision, model) / "last-used", str(time.time()).encode()
        )
    except OSError:
        # Failure to maintain advisory cleanup metadata must not lose dictation.
        pass


def cache_info(now=None):
    """Count allocated bytes without following links; never read audio or model data."""
    now = time.time() if now is None else now
    root = model_directory()
    result = {"bytes": 0, "exists": False, "unused_days": None}
    if not root.exists():
        return result
    if root.is_symlink():
        raise ValueError("The model cache must not be a symbolic link.")
    seen = set()
    for folder, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [name for name in dirs if not (Path(folder) / name).is_symlink()]
        for name in files:
            info = (Path(folder) / name).lstat()
            if not stat.S_ISREG(info.st_mode):
                continue
            result["exists"] = True
            identity = (info.st_dev, info.st_ino)
            if identity not in seen:
                result["bytes"] += info.st_blocks * 512
                seen.add(identity)
    dates = []
    for model, precision in (
        (model, precision)
        for model in LOCAL_MODELS
        for precision in LOCAL_MODELS[model]["sizes"]
    ):
        pointer = variant_directory(root, precision, model) / "current.json"
        if not pointer.is_file():
            continue
        timestamp = pointer.stat().st_mtime
        marker = pointer.parent / "last-used"
        if marker.exists():
            try:
                recorded = float(marker.read_text())
                if not 0 < recorded <= now:
                    return result  # Unknown age: never claim the model is unused.
                timestamp = max(timestamp, recorded)
            except (OSError, ValueError):
                return result
        dates.append(timestamp)
    if dates:
        result["unused_days"] = max(0, int((now - max(dates)) / 86400))
    return result


def format_size(size):
    if size >= 1024**3:
        return f"{size / 1024**3:.1f} GiB"
    return f"{size / 1024**2:.1f} MiB"


def packaged():
    return Path(__file__).resolve().is_relative_to("/usr/lib/dictadesk")


def model_directory(*, create=False):
    if not packaged():
        path = directory("CACHE") / "models"
        if create:
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
        return path
    # A sticky root lets users create private UID directories without elevation.
    # Never fall back into home after the package has been removed.
    root = SYSTEM_CACHE.lstat()
    if not stat.S_ISDIR(root.st_mode) or root.st_uid != 0:
        raise ValueError("The application model cache is unavailable. Reinstall DictaDesk.")
    path = SYSTEM_CACHE / str(os.getuid())
    if create:
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            pass
    if path.exists() or path.is_symlink():
        info = path.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            raise ValueError("The private model cache has invalid ownership or permissions.")
    return path


def downloaded(precision, model="parakeet"):
    try:
        return (
            variant_directory(model_directory(), precision, model) / "current.json"
        ).is_file()
    except (OSError, ValueError):
        return False


def remove_downloads(model=None):
    path = model_directory()
    if model is not None:
        if model not in LOCAL_MODELS:
            raise ValueError("Choose a supported local model.")
        # Validate before any deletion; remove only this model's known variants.
        variants = [
            variant_directory(path, precision, model)
            for precision in LOCAL_MODELS[model]["sizes"]
        ]
        for variant in variants:
            if variant.exists() or variant.is_symlink():
                _remove_directory(variant)
        return
    _remove_directory(path)


def _remove_directory(path):
    if not path.exists() and not path.is_symlink():
        return
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise ValueError("Refusing to remove a model cache with unexpected ownership or type.")
    # Python's descriptor-based rmtree never follows symlinks to other directories.
    if not shutil.rmtree.avoids_symlink_attacks:
        raise RuntimeError("Safe model removal is unavailable on this platform.")
    shutil.rmtree(path)
