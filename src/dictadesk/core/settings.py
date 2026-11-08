# SPDX-License-Identifier: GPL-3.0-only
"""Validated preferences, compatible legacy reads and atomic, private storage."""

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

# Legacy service choices are upgraded on read; unrelated settings and the
# original file stay intact until the user saves preferences.
LEGACY_REMOTE_MODELS = (
    "gpt-4o-mini-transcribe",
    "gpt-4o-transcribe",
    "whisper-1",
)


def directory(kind):
    defaults = {
        "CONFIG": ".config",
        "CACHE": ".cache",
        "DATA": ".local/share",
        "STATE": ".local/state",
    }
    return Path(os.environ.get(f"XDG_{kind}_HOME", Path.home() / defaults[kind])) / "dictadesk"


@dataclass
class Preferences:
    backend: str = "local"
    hands_free: bool = False
    wake_phrase: str = "Clanker"
    command_sound: bool = True
    tts_enabled: bool = False
    tts_engine: str = "piper"
    tts_model: str = "en_US-lessac-medium"
    tts_phrase: str = "Hello"
    volume_step: int = 5
    brightness_step: int = 5
    local_model: str = "parakeet"
    shortcut: str = "<Super>v"
    continuous_shortcut: str = "<Super><Shift>v"
    continuous_minutes: int = 60
    continuous_live: bool = True
    cancel_shortcut: str = "<Super><Shift>x"
    paste_shortcut: str = "<Super><Shift>p"
    microphone: str = ""
    host_api: str = ""
    cloud_model: str = "gpt-transcribe"
    language: str = ""
    device: str = "cpu"
    quantization: str = "int8"
    idle_seconds: int = 300
    max_seconds: int = 180
    preview_pauses: bool = False
    pause_seconds: float = 1.0
    sentence_pause_seconds: float = 4.0
    silence_stop_seconds: int = 20
    silence_threshold: float = 0.015
    auto_paste: bool = True
    symbolic: bool = True
    autostart: bool = False
    spoken_punctuation: bool = False
    vocabulary: list = field(default_factory=list)

    def validate(self):
        if self.backend not in ("local", "openai"):
            raise ValueError("Choose a supported backend.")
        from .tts import ENGINES, VOICES

        if (
            self.tts_engine not in ENGINES
            or self.tts_model not in VOICES
            or VOICES[self.tts_model].engine != self.tts_engine
        ):
            raise ValueError("Choose a supported speech engine and matching voice.")
        if (
            not isinstance(self.tts_phrase, str)
            or not self.tts_phrase.strip()
            or len(self.tts_phrase) > 120
            or not self.tts_phrase.isprintable()
        ):
            raise ValueError("Enter a spoken acknowledgment of 1–120 printable characters.")
        from .voice_commands import words

        if (
            not isinstance(self.wake_phrase, str)
            or not 1 <= len(words(self.wake_phrase)) <= 12
            or len(self.wake_phrase) > 120
        ):
            raise ValueError("Enter a wake phrase of 1–12 words (up to 120 characters).")
        from .models import LOCAL_MODELS, REMOTE_MODELS

        if self.local_model not in LOCAL_MODELS:
            raise ValueError("Choose a supported local model.")
        if self.device not in ("cpu", "cuda"):
            raise ValueError("Choose CPU or CUDA for local processing.")
        if self.quantization not in ("int8", "float32"):
            raise ValueError("Choose Int8 or Float32 for the local model.")
        if self.quantization not in LOCAL_MODELS[self.local_model]["sizes"]:
            raise ValueError("This model is available in Int8 precision only.")
        if not isinstance(self.cloud_model, str) or self.cloud_model not in REMOTE_MODELS:
            raise ValueError("Choose a supported transcription model.")
        for name in (
            "shortcut",
            "continuous_shortcut",
            "cancel_shortcut",
            "paste_shortcut",
            "microphone",
            "host_api",
            "language",
        ):
            if not isinstance(getattr(self, name), str):
                raise ValueError(f"Invalid {name} setting.")
        from ..desktop.keyboard import Chord

        chords = [
            Chord.parse(getattr(self, name))
            for name in ("shortcut", "continuous_shortcut", "cancel_shortcut", "paste_shortcut")
        ]
        if len({(c.key, c.mask) for c in chords}) != 4:
            raise ValueError("Shortcuts must be distinct.")
        if self.language and (
            len(self.language) > 12 or not self.language.replace("-", "").isalpha()
        ):
            raise ValueError("Use a language code such as en or de.")
        for name in (
            "hands_free",
            "command_sound",
            "tts_enabled",
            "auto_paste",
            "symbolic",
            "autostart",
            "preview_pauses",
            "continuous_live",
            "spoken_punctuation",
        ):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"Invalid {name} setting.")
        for name, low, high in (
            ("volume_step", 1, 100),
            ("brightness_step", 1, 100),
            ("idle_seconds", 0, 86400),
            ("max_seconds", 1, 600),
            ("silence_stop_seconds", 0, 600),
            ("continuous_minutes", 1, 240),
        ):
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"{name} must be between {low} and {high}.")
        for name, low, high in (
            ("pause_seconds", 0.3, 5),
            ("sentence_pause_seconds", 1, 10),
            ("silence_threshold", 0.001, 0.2),
        ):
            value = getattr(self, name)
            if type(value) not in (int, float) or not low <= value <= high:
                raise ValueError(f"{name} must be between {low} and {high}.")
        if not isinstance(self.vocabulary, list) or len(self.vocabulary) > 1000:
            raise ValueError("Vocabulary must contain at most 1,000 entries.")
        seen = set()
        for pair in self.vocabulary:
            if (
                not isinstance(pair, list)
                or len(pair) != 2
                or not all(
                    isinstance(x, str) and x.strip() and len(x) <= 500 and "\n" not in x
                    for x in pair
                )
            ):
                raise ValueError(
                    "Each vocabulary entry needs a word or phrase and its replacement."
                )
            if pair[0].casefold() in seen:
                raise ValueError("Remove duplicate vocabulary entries.")
            seen.add(pair[0].casefold())
        return self


def private_write(path, data):
    """Publish a complete file; leave the previous version intact on failure."""
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class SettingsStore:
    def __init__(self, path=None):
        self.path = Path(path) if path else directory("CONFIG") / "preferences.json"
        self.problem = ""
        self.damaged = None

    def read(self):
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return Preferences()
        except OSError as exc:
            self.problem = f"Cannot read settings: {exc}"
            return Preferences()
        try:
            data = json.loads(raw)
            if not isinstance(data, dict) or data.get("schema") != 1:
                raise ValueError("Unknown settings format")
            if not isinstance(data["preferences"], dict):
                raise ValueError("Invalid preferences object")
            values = dict(data["preferences"])
            # Earlier versions offered hold/toggle. Both now use tap-to-start;
            # retire that setting without resetting any other preferences.
            if "mode" in values and values.pop("mode") not in ("hold", "toggle"):
                raise ValueError("Invalid legacy shortcut mode")
            if values.get("cloud_model") in LEGACY_REMOTE_MODELS:
                from .models import DEFAULT_REMOTE_MODEL

                values["cloud_model"] = DEFAULT_REMOTE_MODEL
            return Preferences(**values).validate()
        except (ValueError, TypeError, KeyError) as exc:
            self.damaged = raw
            self.problem = f"Settings need recovery: {exc}"
            return Preferences()

    def save(self, prefs):
        if self.problem:
            raise ValueError("Back up the unreadable settings before saving changes.")
        prefs.validate()
        private_write(
            self.path,
            json.dumps(
                {"schema": 1, "preferences": asdict(prefs)}, ensure_ascii=False, indent=2
            ).encode(),
        )

    def recover(self):
        if self.damaged is None:
            raise ValueError("Settings cannot be backed up until the file can be read.")
        if self.path.read_bytes() != self.damaged:
            raise ValueError("Settings changed on disk. Restart to inspect the new file.")
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
        backup = self.path.with_name(f"preferences.broken-{stamp}.json")
        fd = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(self.damaged)
            stream.flush()
            os.fsync(stream.fileno())
        previous = self.problem
        self.problem = ""
        try:
            self.save(Preferences())
        except Exception:
            self.problem = previous
            raise
        self.damaged = None
        return backup
