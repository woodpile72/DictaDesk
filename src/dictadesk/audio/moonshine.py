# SPDX-License-Identifier: GPL-3.0-only
"""English Moonshine Tiny behind the existing disposable speech-worker boundary."""

import hashlib
from pathlib import Path
import urllib.request
import wave

# Explicit original Tiny model, not the upstream library's changing default.
# Official English weights are MIT licensed. Files were verified during our
# evaluation; future model revisions require an explicit checksum update.
BASE_URL = "https://download.moonshine.ai/model/tiny-en/quantized/tiny-en"
FILES = {
    "encoder_model.ort": "94e90a4654fc45cdfedb77c4c08e1739f48862998e58fada384b25118134f221",
    "decoder_model_merged.ort": "cf524c4862d36e9e5ab032eddc73637efd822d70e868ac575cf1a46e1e4708a0",
    "tokenizer.bin": "6884b35fd6377d4c4d32336a0bc152f36b64d1e45b6503683cdc238250a8472d",
}


def download(folder):
    folder.mkdir(parents=True, mode=0o700)
    for name, expected in FILES.items():
        digest = hashlib.sha256()
        from .. import __version__

        request = urllib.request.Request(
            f"{BASE_URL}/{name}", headers={"User-Agent": f"DictaDesk/{__version__}"}
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            with (folder / name).open("xb") as stream:
                while block := response.read(1024 * 1024):
                    stream.write(block)
                    digest.update(block)
        if digest.hexdigest() != expected:
            raise ValueError("Moonshine download verification failed. Retry the download.")


class Moonshine:
    def __init__(self, folder):
        from moonshine_voice import ModelArch, Transcriber

        self.model = Transcriber(model_path=folder, model_arch=ModelArch.TINY)

    def recognize(self, audio, sample_rate=16000):
        import numpy as np

        if isinstance(audio, (str, Path)):
            with wave.open(str(audio)) as wav:
                if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) != (
                    16000,
                    1,
                    2,
                ):
                    raise ValueError("Moonshine requires mono 16 kHz PCM16 recording audio.")
                audio = (
                    np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2").astype(
                        np.float32
                    )
                    / 32768
                )
        if sample_rate != 16000:
            raise ValueError("Moonshine requires 16 kHz recording audio.")
        result = self.model.transcribe_without_streaming(audio.tolist(), sample_rate=16000)
        return " ".join(line.text for line in result.lines).strip()


def load(folder, *, allow_download=False):
    if not folder.exists() and allow_download:
        download(folder)
    if not all((folder / name).is_file() for name in FILES):
        raise ValueError("The Moonshine files are incomplete. Use Repair Model.")
    return Moonshine(folder)
