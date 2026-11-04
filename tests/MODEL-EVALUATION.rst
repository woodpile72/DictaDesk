Local model evaluation — 2026-11-03
===================================

Decision
--------

Keep Parakeet as the default. Offer Moonshine Tiny (English) second, then
Whisper Base and Whisper Tiny. Expose the tradeoffs in Preferences under
“How do I choose a model?”. Existing selections remain unchanged. Model
switching keeps downloaded models; removal is a separate action.

The original Moonshine Tiny English model is the supported option. The separate
Tiny Streaming variant remains evaluation-only: changing draft text is not
safe to insert immediately into arbitrary applications.

Small English comparison
------------------------

CPU: AMD Ryzen 5 7600X, 12 logical CPUs. Python 3.12.3, NumPy 2.5.4,
ONNX Runtime 1.31.1, onnx-asr 0.12.0, Hugging Face Hub 2.4.0,
Moonshine Voice 0.1.5. All measurements below use Int8/quantized models.

.. list-table:: One desktop, 13 human speech clips
   :header-rows: 1

   * - Model
     - Word error rate
     - Model MiB
     - Peak process MiB
     - Median inference seconds
   * - parakeet
     - 7.3%
     - 639.4
     - 1339
     - 0.189
   * - moonshine-tiny
     - 14.6%
     - 41.9
     - 208
     - 0.108
   * - whisper-base
     - 14.0%
     - 103.2
     - 427
     - 0.446
   * - whisper-tiny
     - 27.5%
     - 40.0
     - 502
     - 0.290
   * - whisper-tiny-en
     - 27.5%
     - 40.0
     - 503
     - 0.316
   * - moonshine-tiny-streaming
     - 18.5%
     - 43.1
     - 265
     - 0.542

The clips are every sixth entry from the fixed LibriSpeech dummy validation
split: 13 clips, one speaker (1272), 74.18 seconds, 178 normalized reference
words. Selection preceded the results. Case and punctuation are normalized;
insertions, deletions and substitutions count as word errors. Each clip runs
three times; accuracy uses the first run and timings use the median.

This is a small diagnostic sample, not a representative benchmark. It does
not establish multilingual accuracy, minimum RAM requirements, or performance
on another computer. Peak process memory includes runtime allocations; model
size on disk is not a RAM estimate. Float32 was tested for download and reload,
not ranked for accuracy. Raw results, references, hashes and transcripts are
in model-evaluation-results/2026-11-03/.

Additional checks
-----------------

Synthetic eSpeak clips cover ordinary text, technical terms, spoken punctuation,
background noise and silence. A public JFK sample checks actual transcription.
Whisper Tiny omitted words and repeated unrelated text on synthetic recordings;
forcing English did not improve its human-sample score. Whisper Base and forced-
English Tiny also produced text from raw digital silence. DictaDesk's silence
safeguards are separate from this raw-model test.

Offline replay exercises the existing stable-prefix policy. Its first-output
times combine audio position and serial inference time; they are not measured
desktop paste latency. Native Moonshine streaming produced drafts before a
completed phrase, but drafts changed. The application retains its insertion
and focus safeguards.

Actual worker tests pass for fresh downloads, checksum verification, offline
reload, Moonshine Int8 and Whisper Tiny Int8/Float32. Real Moonshine pause-based,
continuous and window dictation tests pass. Whisper Tiny's delivery-only test
passes ordering, insertion-before-Stop and clipboard safeguards; it explicitly
does not pass an accuracy assertion for the synthetic sample.

Hugging Face external weights
----------------------------

A tiny original ONNX fixture reproduces the reported external-data path failure
when model and weight symlinks resolve into different Hugging Face blob folders.
Materializing regular files together in the app-owned model generation fixes
the runtime boundary error. Tests verify load/reload, hashes, and rejection of
missing or altered weights. Runtime path checks are not disabled.

A fresh download also exposed an HTTP Brotli decoder mismatch with the system's
optional decoder. The worker requests gzip/deflate for Hugging Face downloads,
which the HTTP stack supports without that optional dependency. Moonshine uses
its official download endpoint, a versioned app User-Agent, pinned SHA-256 hashes
and the same atomic generation publication safeguards.

Reproduction
------------

See model_evaluation.py --help. Download tests are explicit and use isolated
.qa directories; no microphone or paid service is used::

    PYTHONPATH=src .venv/bin/python tests/model_evaluation.py --prepare
    PYTHONPATH=src .venv/bin/python tests/model_evaluation.py --prepare-human
    PYTHONPATH=src .venv/bin/python tests/model_evaluation.py --download-models
    PYTHONPATH=src .venv/bin/python tests/model_evaluation.py --model parakeet
    PYTHONPATH=src .venv/bin/python tests/real_model_options.py --download-models

The recorded comparison used the packaged NumPy/runtime versions. The evaluation
script requires optional evaluation dependencies for the corpus and Moonshine
streaming experiment; these are not application features or model downloads
bundled in the Debian package.

Sources and attribution
-----------------------

* Parakeet model card: https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3
* Whisper models: https://github.com/openai/whisper
* Whisper Tiny ONNX: https://huggingface.co/onnx-community/whisper-tiny
  revision ff4177021cc41f7db950912b73ea4fdf7d01d8e7.
* Moonshine Tiny English: https://huggingface.co/moonshine-ai/moonshine-tiny
  (MIT); https://moonshine-voice.readthedocs.io/en/latest/models/available-models/
* LibriSpeech, Vassil Panayotov, Guoguo Chen, Daniel Povey and Sanjeev Khudanpur:
  https://www.openslr.org/12 (CC BY 4.0). The subset is distributed through
  https://huggingface.co/datasets/hf-internal-testing/librispeech_asr_dummy
  revision 5be91486e11a2d616f4ec5db8d3fd248585ac07a. References are normalized for
  scoring; source audio is not redistributed here.
* JFK sample: https://github.com/ggml-org/whisper.cpp/blob/4da27c41cebadb3cbbcd2563e9c7cbc8d84bdcad/samples/jfk.wav
* Native dialog guidance:
  https://developer.gnome.org/hig/patterns/feedback/dialogs.html
