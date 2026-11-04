# SPDX-License-Identifier: GPL-3.0-only
"""Opt-in CPU model experiment; generated/public audio only, never a microphone.

Run --prepare once, then each --model in a separate process without concurrent
benchmarks. Moonshine's optional evaluation-only wheel lives under ROOT/vendor.
Results are feasibility checks, not an accuracy benchmark across speakers.
"""

import argparse
import ctypes as c
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import resource
import statistics
import subprocess
import sys
import time
import urllib.request
import wave

import numpy as np

PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT / ".qa/model-evaluation"
WHISPER_REVISION = "ff4177021cc41f7db950912b73ea4fdf7d01d8e7"
JFK_URL = (
    "https://raw.githubusercontent.com/ggml-org/whisper.cpp/"
    "4da27c41cebadb3cbbcd2563e9c7cbc8d84bdcad/samples/jfk.wav"
)
LIBRISPEECH_REVISION = "5be91486e11a2d616f4ec5db8d3fd248585ac07a"


def read_audio(path):
    with wave.open(str(path)) as wav:
        assert (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) == (16000, 1, 2)
        return np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2").copy()


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_audio(path, samples, rate=16000):
    with wave.open(str(path), "wb") as wav:
        wav.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        wav.writeframes(np.asarray(samples, dtype="<i2").tobytes())


def prepare(root):
    root.mkdir(parents=True, exist_ok=True)
    lib = c.CDLL("libespeak-ng.so.1")
    lib.espeak_Initialize.argtypes = [c.c_int, c.c_int, c.c_char_p, c.c_int]
    rate = lib.espeak_Initialize(2, 0, None, 0x8000)
    assert rate > 0
    blocks = []
    callback_type = c.CFUNCTYPE(c.c_int, c.POINTER(c.c_short), c.c_int, c.c_void_p)

    @callback_type
    def collect(samples, count, events):
        if samples and count:
            blocks.append(c.string_at(samples, count * 2))
        return 0

    lib.espeak_SetSynthCallback.argtypes = [callback_type]
    lib.espeak_SetSynthCallback(collect)
    lib.espeak_SetVoiceByName.argtypes = [c.c_char_p]
    lib.espeak_SetVoiceByName(b"en-us")
    lib.espeak_Synth.argtypes = [
        c.c_char_p,
        c.c_size_t,
        c.c_uint,
        c.c_int,
        c.c_uint,
        c.c_uint,
        c.c_void_p,
        c.c_void_p,
    ]
    references = {
        "ordinary": "Please send the revised document tomorrow morning. I will check the spelling before the meeting.",
        "technical": "Open the terminal and install the package. The microphone uses a local model without sending audio to a remote server.",
        "commands": "Hello comma this is a test period New paragraph Open parenthesis another sentence close parenthesis dot dot dot",
    }
    records = []
    for name, text in references.items():
        blocks.clear()
        encoded = text.encode()
        assert lib.espeak_Synth(encoded, len(encoded) + 1, 0, 1, 0, 1, None, None) == 0
        original = root / f"{name}-original.wav"
        write_audio(original, np.frombuffer(b"".join(blocks), dtype="<i2"), rate)
        path = root / f"{name}.wav"
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(original),
                "-ar",
                "16000",
                str(path),
            ],
            check=True,
        )
        records.append({"name": name, "reference": text, "source": "eSpeak NG en-us"})
    source = root / "jfk.wav"
    if not source.exists():
        with urllib.request.urlopen(JFK_URL, timeout=30) as response:
            source.write_bytes(response.read())
    records.append(
        {
            "name": "jfk",
            "reference": "And so my fellow Americans ask not what your country can do for you ask what you can do for your country",
            "source": JFK_URL,
        }
    )
    clean = read_audio(root / "ordinary.wav").astype(np.float64)
    # Fixed 20 dB SNR additive white noise, not overlapping human speech.
    noise = np.random.default_rng(42).normal(0, np.sqrt(np.mean(clean**2)) / 10, len(clean))
    write_audio(root / "ordinary-noise.wav", np.clip(clean + noise, -32768, 32767))
    records.append(
        {
            "name": "ordinary-noise",
            "reference": references["ordinary"],
            "source": "ordinary + fixed 20 dB white noise",
        }
    )
    write_audio(root / "silence.wav", np.zeros(5 * 16000, dtype=np.int16))
    records.append({"name": "silence", "reference": "", "source": "digital silence"})
    for item in records:
        path = root / (item["name"] + ".wav")
        item["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        item["duration_s"] = len(read_audio(path)) / 16000
    (root / "samples.json").write_text(json.dumps(records, indent=2) + "\n")


def prepare_human(root):
    # Evaluation-only dependency; never bundled into DictaDesk.
    sys.path.insert(0, str(root / "vendor"))
    import pyarrow.parquet as pq

    url = (
        "https://huggingface.co/datasets/hf-internal-testing/librispeech_asr_dummy/resolve/"
        + LIBRISPEECH_REVISION
        + "/clean/validation-00000-of-00001.parquet"
    )
    archive = root / "librispeech.parquet"
    if not archive.exists():
        with urllib.request.urlopen(url, timeout=60) as response:
            archive.write_bytes(response.read())
    rows = pq.read_table(archive).to_pylist()
    records = json.loads((root / "samples.json").read_text())
    records = [r for r in records if not r["name"].startswith("librispeech-")]
    # Fixed selection made before examining recognition results. All 13 clips
    # come from one speaker: useful for feasibility, not population accuracy.
    for row in rows[::6]:
        name = "librispeech-" + row["id"]
        flac = root / (name + ".flac")
        flac.write_bytes(row["audio"]["bytes"])
        path = root / (name + ".wav")
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(flac),
                "-ar",
                "16000",
                "-ac",
                "1",
                str(path),
            ],
            check=True,
        )
        duration = len(read_audio(path)) / 16000
        assert duration <= 25, "Keep clips within every candidate's context limit"
        records.append(
            {
                "name": name,
                "reference": row["text"],
                "source": url,
                "speaker_id": row["speaker_id"],
                "sha256": digest(path),
                "duration_s": duration,
            }
        )
    (root / "samples.json").write_text(json.dumps(records, indent=2) + "\n")


def download_models(root):
    from dictadesk.audio.worker import configure_hub_downloads, load_model
    from huggingface_hub import snapshot_download

    configure_hub_downloads()
    snapshot_download(
        "onnx-community/whisper-tiny",
        revision=WHISPER_REVISION,
        local_dir=root / "models/whisper-tiny",
        allow_patterns=[
            "config.json",
            "vocab.json",
            "added_tokens.json",
            "onnx/encoder_model_quantized.onnx",
            "onnx/decoder_model_merged_quantized.onnx",
        ],
    )
    for name, cache in (
        ("parakeet", PROJECT / ".qa/real-speech/models"),
        ("whisper-base", PROJECT / ".qa/whisper-models"),
    ):
        load_model({"cache": str(cache), "precision": "int8", "local_model": name})
    sys.path.insert(0, str(root / "vendor"))
    from moonshine_voice import ModelArch
    from moonshine_voice.download import find_model_info, download_model_from_info

    for name, arch in (
        ("moonshine-tiny", ModelArch.TINY),
        ("moonshine-tiny-streaming", ModelArch.TINY_STREAMING),
    ):
        info = find_model_info("en", arch)
        path, actual_arch = download_model_from_info(info, cache_root=root / "models")
        (root / f"{name}.json").write_text(
            json.dumps({"path": path, "arch": int(actual_arch), "info": info}, indent=2)
        )


def distance(reference, hypothesis):
    """Word edit distance; ignore case/punctuation, retain literal spoken commands."""
    ref = re.findall(r"\w+", reference.casefold())
    hyp = re.findall(r"\w+", hypothesis.casefold())
    row = list(range(len(hyp) + 1))
    for i, word in enumerate(ref, 1):
        next_row = [i]
        for j, other in enumerate(hyp, 1):
            next_row.append(min(next_row[-1] + 1, row[j] + 1, row[j - 1] + (word != other)))
        row = next_row
    return {
        "word_errors": row[-1],
        "reference_words": len(ref),
        "wer": row[-1] / len(ref) if ref else None,
    }


def load(name, root, language=None):
    started = time.perf_counter()
    if name.startswith("moonshine"):
        sys.path.insert(0, str(root / "vendor"))
        from moonshine_voice import ModelArch, Transcriber

        metadata = json.loads((root / f"{name}.json").read_text())
        folder = Path(metadata["path"])
        model = Transcriber(folder, ModelArch(metadata["arch"]))

        def recognize(pcm):
            result = model.transcribe_without_streaming(
                (pcm.astype(np.float32) / 32768).tolist()
            )
            return " ".join(line.text for line in result.lines).strip()
    else:
        import onnx_asr

        if name == "whisper-tiny":
            folder = root / "models/whisper-tiny"
            runtime, quantization = "onnx-community/whisper-tiny", "quantized"
        else:
            from dictadesk.core.models import LOCAL_MODELS

            parent = (
                PROJECT / ".qa/real-speech/models/int8"
                if name == "parakeet"
                else PROJECT / ".qa/whisper-models/whisper-base/int8"
            )
            folder = parent / json.loads((parent / "current.json").read_text())["generation"]
            runtime, quantization = LOCAL_MODELS[name]["runtime"], "int8"
        model = onnx_asr.load_model(
            runtime, str(folder), quantization=quantization, providers=["CPUExecutionProvider"]
        )

        def recognize(pcm):
            kwargs = {"language": language} if name.startswith("whisper") and language else {}
            return model.recognize(
                pcm.astype(np.float32) / 32768, sample_rate=16000, **kwargs
            ).strip()

    loaded = time.perf_counter() - started
    files = [p for p in folder.rglob("*") if p.is_file() and ".cache" not in p.parts]
    metadata = {
        "load_s": loaded,
        "model_bytes": sum(p.stat().st_size for p in files),
        "files": {str(p.relative_to(folder)): digest(p) for p in files},
    }
    return recognize, metadata


def native_stream(root, pcm):
    """Exercise Moonshine's own incremental API, separately from app-style replay."""
    from moonshine_voice import ModelArch, Transcriber
    from moonshine_voice.transcriber import LineTextChanged, LineCompleted

    info = json.loads((root / "moonshine-tiny-streaming.json").read_text())
    events = []
    audio_at = 0

    def listener(event):
        if isinstance(event, (LineTextChanged, LineCompleted)):
            events.append(
                {"event": type(event).__name__, "audio_at_s": audio_at, "text": event.line.text}
            )

    with Transcriber(info["path"], ModelArch(info["arch"])) as model:
        model.add_listener(listener)
        model.start()
        started = time.perf_counter()
        for start in range(0, len(pcm), 1600):
            audio_at = min(start + 1600, len(pcm)) / 16000
            model.add_audio((pcm[start : start + 1600].astype(np.float32) / 32768).tolist())
        model.stop()
        elapsed = time.perf_counter() - started
    return {"processing_s": elapsed, "audio_s": len(pcm) / 16000, "events": events}


def replay(recognize, pcm):
    """Replay app segment/preview policy offline; never send keys or clipboard data.

    'ready_at' simulates serialized inference against audio time, excluding actual
    IPC/UI/paste overhead. It is not measured end-to-end desktop latency.
    """
    from dictadesk.audio.continuous import Phrases
    from dictadesk.core.incremental import StablePrefix

    splitter, stable = Phrases(0.8, 0.015), StablePrefix()
    next_preview, ready_at, first_stable = 48000, 0.0, None
    updates = []
    for start in range(0, len(pcm), 1600):
        at = min(start + 1600, len(pcm)) / 16000
        final = splitter.add(pcm[start : start + 1600].tobytes())
        if final:
            data = final
        elif len(splitter.audio) >= next_preview:
            data = bytes(splitter.audio)
            next_preview = len(data) + 48000
        else:
            continue
        before = time.perf_counter()
        text = recognize(np.frombuffer(data, dtype="<i2"))
        elapsed = time.perf_counter() - before
        ready_at = max(at, ready_at) + elapsed
        _, conflict = stable.update(text, final=bool(final))
        if first_stable is None and stable.committed and not conflict:
            first_stable = ready_at
        updates.append(
            {
                "audio_at_s": at,
                "inference_s": elapsed,
                "final": bool(final),
                "text": text,
                "committed": stable.committed,
                "conflict": conflict,
            }
        )
        if final:
            stable, next_preview = StablePrefix(), 48000
    return {
        "estimated_first_stable_s": first_stable,
        "conflicts": sum(u["conflict"] for u in updates),
        "updates": updates,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--prepare-human", action="store_true")
    parser.add_argument("--download-models", action="store_true")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument(
        "--model",
        choices=[
            "parakeet",
            "whisper-base",
            "whisper-tiny",
            "moonshine-tiny",
            "moonshine-tiny-streaming",
        ],
    )
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--language", choices=["en"])
    args = parser.parse_args()
    if args.prepare:
        prepare(args.root)
    if args.prepare_human:
        prepare_human(args.root)
    if args.download_models:
        download_models(args.root)
    if not args.model:
        return
    recognize, metadata = load(args.model, args.root, args.language)
    result = {
        "model": args.model,
        "language": args.language,
        "cpu": platform.processor(),
        "logical_cpus": os.cpu_count(),
        "python": platform.python_version(),
        "versions": {
            name: importlib.metadata.version(name)
            for name in ("numpy", "onnxruntime", "onnx-asr", "huggingface-hub")
        },
        "cpu_model": next(
            (
                line.split(":", 1)[1].strip()
                for line in Path("/proc/cpuinfo").read_text().splitlines()
                if line.startswith("model name")
            ),
            "unknown",
        ),
        **metadata,
        "cases": [],
    }
    samples = json.loads((args.root / "samples.json").read_text())
    for item in samples:
        pcm = read_audio(args.root / (item["name"] + ".wav"))
        runs = []
        for _ in range(args.repeat):
            started = time.perf_counter()
            text = recognize(pcm)
            runs.append(
                {
                    "seconds": time.perf_counter() - started,
                    "text": text,
                    **distance(item["reference"], text),
                }
            )
        case = {**item, "runs": runs, "median_s": statistics.median(r["seconds"] for r in runs)}
        result["cases"].append(case)
        print(
            args.model,
            item["name"],
            round(case["median_s"], 3),
            runs[-1]["text"][:240],
            flush=True,
        )
    pcm = np.concatenate(
        [
            read_audio(args.root / "ordinary.wav"),
            np.zeros(16000, dtype=np.int16),
            read_audio(args.root / "technical.wav"),
            np.zeros(16000, dtype=np.int16),
        ]
    )
    result["replay"] = replay(recognize, pcm)
    result["peak_rss_mib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    if args.model == "moonshine-tiny-streaming":
        # The native streaming interface has different segmentation semantics;
        # preserve its events and never label replay estimates as desktop latency.
        result["native_stream"] = native_stream(args.root, pcm)
    suffix = "-" + args.language if args.language else ""
    path = args.root / f"result-{args.model}{suffix}.json"
    path.write_text(json.dumps(result, indent=2) + "\n")
    print("Saved", path, flush=True)


if __name__ == "__main__":
    main()
