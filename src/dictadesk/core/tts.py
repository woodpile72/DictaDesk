# SPDX-License-Identifier: GPL-3.0-only
"""Optional speech engines and pinned voices. No download happens on import."""

from dataclasses import dataclass
import ctypes.util
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import platform
import stat
import tempfile
import time
import urllib.request
import zipfile

from .models import model_directory, _remove_directory
from .settings import private_write


@dataclass(frozen=True)
class Artifact:
    url: str
    size: int
    sha256: str
    name: str


@dataclass(frozen=True)
class Voice:
    engine: str
    name: str
    description: str
    files: tuple = ()


ENGINES = {"piper": "Piper — natural voice (download)", "espeak": "eSpeak NG — system voice"}
REVISION = "e7229f7d33a10e145e109b886d661984201ac3c4"
VOICE_BASE = f"https://huggingface.co/rhasspy/piper-voices/resolve/{REVISION}/en/en_US/"
RUNTIME = (
    Artifact(
        "https://files.pythonhosted.org/packages/84/81/0112a7d510911f33018dc24023d1655bb772f84a4e95fe7f0180f66bbd17/piper_tts-1.8.0-cp39-abi3-manylinux_2_17_x86_64.manylinux2014_x86_64.manylinux_2_28_x86_64.whl",
        34131442,
        "25b4d3f31ff70c8fa7151908e00aaa5650cbdf16bca8fcf21299f3941b89a7d3",
        "piper.whl",
    ),
    Artifact(
        "https://files.pythonhosted.org/packages/9a/70/875f4a23bfc4731703a5835487d0d2fb999031bd415e7d17c0ae615c18b7/pathvalidate-3.3.1-py3-none-any.whl",
        24305,
        "5263baab691f8e1af96092fa5137ee17df5bdfbd6cff1fcac4d6ef4bc2e1735f",
        "pathvalidate.whl",
    ),
)


def voice_files(
    folder, ident, size, model_hash, config_size, config_hash, card_size, card_hash
):
    return (
        Artifact(
            VOICE_BASE + folder + "/" + ident + ".onnx", size, model_hash, ident + ".onnx"
        ),
        Artifact(
            VOICE_BASE + folder + "/" + ident + ".onnx.json",
            config_size,
            config_hash,
            ident + ".onnx.json",
        ),
        Artifact(VOICE_BASE + folder + "/MODEL_CARD", card_size, card_hash, "MODEL_CARD"),
    )


VOICES = {
    "en_US-lessac-medium": Voice(
        "piper",
        "Lessac — English (US), medium",
        "Natural US English · about 63 MB for the voice, plus 34 MB for the optional engine.",
        voice_files(
            "lessac/medium",
            "en_US-lessac-medium",
            63201294,
            "5efe09e69902187827af646e1a6e9d269dee769f9877d17b16b1b46eeaaf019f",
            4885,
            "efe19c417bed055f2d69908248c6ba650fa135bc868b0e6abb3da181dab690a0",
            351,
            "ce49eb457742208166d399a40cdec2c7fa9db77960930031564ab56f12882645",
        ),
    ),
    "en_US-lessac-high": Voice(
        "piper",
        "Lessac — English (US), high",
        "Higher-quality US English · about 114 MB for the voice, plus the optional engine.",
        voice_files(
            "lessac/high",
            "en_US-lessac-high",
            113895201,
            "4cabf7c3a638017137f34a1516522032d4fe3f38228a843cc9b764ddcbcd9e09",
            4883,
            "db42b97d9859f257bc1561b8ed980e7fb2398402050a74ddd6cbec931a92412f",
            347,
            "7671826d947a0ffc11dd76af0bd890d93e956b00358696ad71348f21aa827100",
        ),
    ),
    "en_US-amy-low": Voice(
        "piper",
        "Amy — English (US), low",
        "Alternative US English voice · about 63 MB, plus the optional engine.",
        voice_files(
            "amy/low",
            "en_US-amy-low",
            63104526,
            "a5a91abb7de0f104358a25aded480ddacf1ff0762886325886ec406a2e86aab3",
            4164,
            "2250a9a605b8dc35a116717fadc5056695dd809e34a15d02f72a0f52d53d3ebb",
            273,
            "71b07ded301a96346096a71d8f0d64cbb5f49d5d51004dce28bbb7504e3f65f0",
        ),
    ),
    "en-us": Voice(
        "espeak",
        "English (US) — system",
        "Small, robotic system voice. Uses installed eSpeak NG; no voice-model download.",
    ),
    "en-gb": Voice(
        "espeak",
        "English (UK) — system",
        "Small, robotic system voice. Uses installed eSpeak NG; no voice-model download.",
    ),
}


@lru_cache(maxsize=1)
def espeak_available():
    return bool(ctypes.util.find_library("espeak-ng"))


def download(artifact, path, cancel, progress=lambda *_: None, *, fetch=urllib.request.urlopen):
    digest, count = hashlib.sha256(), 0
    request = urllib.request.Request(artifact.url, headers={"User-Agent": "DictaDesk/1.2.0"})
    if cancel.is_set():
        raise InterruptedError()
    with fetch(request, timeout=10) as source, path.open("wb") as output:
        while data := source.read(256 * 1024):
            if cancel.is_set():
                raise InterruptedError()
            count += len(data)
            if count > artifact.size:
                raise ValueError("The speech download exceeded its expected size.")
            digest.update(data)
            output.write(data)
            progress(count, artifact.size)
    if count != artifact.size or digest.hexdigest() != artifact.sha256:
        raise ValueError("The speech download failed verification. Please try again.")


def unpack(wheel, destination):
    with zipfile.ZipFile(wheel) as archive:
        if sum(item.file_size for item in archive.infolist()) > 80 * 1024 * 1024:
            raise ValueError("The speech engine archive is too large.")
        for item in archive.infolist():
            path = Path(item.filename)
            if (
                path.is_absolute()
                or ".." in path.parts
                or stat.S_ISLNK(item.external_attr >> 16)
                or path.suffix == ".pth"
            ):
                raise ValueError("The speech engine archive has an unsafe entry.")
        archive.extractall(destination)


def manifest(folder):
    result = {}
    for path in sorted(folder.rglob("*")):
        if path.is_symlink():
            raise ValueError("Speech engine files must not be symbolic links.")
        if path.is_file() and path.name != "installed.json":
            with path.open("rb") as stream:
                result[str(path.relative_to(folder))] = [
                    path.stat().st_size,
                    hashlib.file_digest(stream, "sha256").hexdigest(),
                ]
    return result


class SpeechStore:
    def __init__(self, root=None):
        self.custom_root = Path(root) if root is not None else None

    def root(self, *, create=False):
        root = (
            self.custom_root
            if self.custom_root is not None
            else model_directory(create=create) / "tts"
        )
        if create:
            root.mkdir(parents=True, mode=0o700, exist_ok=True)
        if root.exists() or root.is_symlink():
            info = root.lstat()
            if (
                root.is_symlink()
                or not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o700
            ):
                raise ValueError(
                    "The private speech cache has invalid ownership or permissions."
                )
        return root

    def runtime(self):
        return self.root() / "runtime-1.8.0"

    def voice_folder(self, model):
        if model not in VOICES or VOICES[model].engine != "piper":
            raise ValueError("Choose a supported Piper voice.")
        voices = self.root() / "voices"
        if voices.is_symlink():
            raise ValueError("The private speech voice directory must not be a symbolic link.")
        return voices / model

    def installed(self, folder, *, verify=False):
        try:
            if folder.is_symlink():
                return False
            if not verify:
                return (folder / "installed.json").is_file()
            data = json.loads((folder / "installed.json").read_text())
            for name, (size, digest) in data["files"].items():
                path = folder / name
                if (
                    Path(name).is_absolute()
                    or ".." in Path(name).parts
                    or any(p.is_symlink() for p in [path, *path.parents] if p != folder.parent)
                ):
                    return False
                if not path.is_file() or path.stat().st_size != size:
                    return False
                if verify:
                    with path.open("rb") as stream:
                        if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                            return False
            return bool(data["files"])
        except (OSError, ValueError, KeyError, TypeError):
            return False

    def ready(self, engine, model):
        try:
            if model not in VOICES or VOICES[model].engine != engine:
                return False
            if engine == "espeak":
                return espeak_available()
            folder = self.voice_folder(model)
            return (
                platform.machine() in ("x86_64", "amd64")
                and self.installed(self.runtime())
                and (self.runtime() / "piper/espeakbridge.so").is_file()
                and self.installed(folder)
                and all(
                    (folder / file.name).is_file()
                    and (folder / file.name).stat().st_size == file.size
                    for file in VOICES[model].files
                )
            )
        except (OSError, ValueError):
            return False

    def install(self, model, cancel, progress=lambda *_: None):
        if model not in VOICES or VOICES[model].engine != "piper":
            raise ValueError("This voice uses the system eSpeak NG installation.")
        if platform.machine() not in ("x86_64", "amd64"):
            raise ValueError("Optional Piper downloads currently support Linux x86-64.")
        root = self.root(create=True)
        groups = [
            (self.runtime(), RUNTIME, True),
            (self.voice_folder(model), VOICES[model].files, False),
        ]
        needed = [
            (path, files, archive)
            for path, files, archive in groups
            if not self.installed(path, verify=True)
        ]
        total = sum(file.size for _, files, _ in needed for file in files)
        completed = 0
        for target, artifacts, archive in needed:
            with tempfile.TemporaryDirectory(prefix=".speech-download-", dir=root) as staging:
                stage = Path(staging)
                content = stage / "content"
                content.mkdir(mode=0o700)
                for artifact in artifacts:
                    path = stage / artifact.name if archive else content / artifact.name
                    download(
                        artifact,
                        path,
                        cancel,
                        lambda amount, _size: progress(completed + amount, total),
                    )
                    completed += artifact.size
                    if archive:
                        unpack(path, content)
                if cancel.is_set():
                    raise InterruptedError()
                private_write(
                    content / "installed.json",
                    json.dumps({"files": manifest(content)}).encode(),
                )
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                if target.exists() or target.is_symlink():
                    _remove_directory(target)
                content.rename(target)
        progress(total, total)

    def verify(self, engine, model):
        if not self.ready(engine, model):
            raise ValueError(
                "Download the selected speech engine and voice first, or install system eSpeak NG."
            )
        if engine == "piper" and not (
            self.installed(self.runtime(), verify=True)
            and self.installed(self.voice_folder(model), verify=True)
        ):
            raise ValueError(
                "The speech engine or voice changed. Remove its downloads and download again."
            )

    def remove(self):
        root = self.root()
        if root.exists():
            _remove_directory(root)

    def note_use(self):
        try:
            private_write(self.root() / "last-used", str(time.time()).encode())
        except OSError:
            pass
