.. image:: dictadesk-iconset/dictadesk.svg
   :alt: DictaDesk microphone application icon
   :width: 128

DictaDesk
=========

**Desktop Dictation for Linux**

Dictate directly into text fields using a keyboard shortcut. DictaDesk runs in
the system tray, with no main application window. Built with Python, GTK 4 and
libadwaita for Cinnamon. Local transcription is the default.

`Download DictaDesk 1.2.0 for Linux (.deb) <https://github.com/woodpile72/DictaDesk/releases/download/v1.2.0/dictadesk_1.2.0_amd64.deb>`_
· `Release notes <https://github.com/woodpile72/DictaDesk/releases/tag/v1.2.0>`_
· `Report an issue <https://github.com/woodpile72/DictaDesk/issues>`_

.. image:: media/screenshots/preferences-local.png
   :alt: DictaDesk Preferences with compact usage instructions above the settings
   :width: 880

Screenshots show the actual DictaDesk 1.2.0 interface in an isolated demonstration
profile, with simulated readiness and sample text. No personal transcripts or
credentials are shown.

Install
-------

Developed and tested on **Linux Mint 22.3 Cinnamon, X11, x86-64 (amd64)**.
Download the Debian package and
`SHA256SUMS <https://github.com/woodpile72/DictaDesk/releases/download/v1.2.0/SHA256SUMS>`_
into the same folder, then run::

    sha256sum --ignore-missing -c SHA256SUMS
    sudo apt install ./dictadesk_1.2.0_amd64.deb

Check that the checksum reports ``OK``, then launch **DictaDesk** from your
application menu. Use the tray menu to open Preferences and download a local
model before recording.
The package includes its Python runtime dependencies; models download separately.
It requires Python 3.12, GTK 4.14+ and libadwaita 1.5+. It uses the CPU and does
not require an NVIDIA card. Quit the app before future upgrades.

Start dictating
---------------

1. Wait for **Ready**. Select your microphone in Preferences if needed; the
   microphone test helps check input and recognition.
2. Click a text field in another application. Press **Super+V**, speak, then
   press **Super+V** again or **Escape** to finish. Text is inserted at pauses.
   By default, recording stops after **20 seconds of continuous silence**. Speaking
   again before then resets the timer.
3. For longer dictation, **Super+Shift+V** starts continuous mode with stable
   text insertion while speaking. Press it again or **Escape** to finish.

Short pauses show text without finalizing the sentence ending. By default,
DictaDesk waits 4 seconds of silence to finalize it; stopping also finalizes it.
Silence-based auto-stop applies only to the ordinary dictation shortcut;
continuous mode continues until stopped.
All three timings are adjustable under **Preferences → Dictation Timing**.
Lower **Sentence Ending Pause** for shorter gaps between sentences, or raise it
to allow longer thinking pauses. **Stop After Silence** accepts 1–600 seconds;
set it to **0 (Off)** to keep recording through quiet gaps. The separate
maximum recording length still applies. Existing saved values are preserved.
Explicit spoken punctuation is respected when that option is enabled. Recognition can still infer punctuation
within a phrase. A few trailing words stay provisional so recognition can revise
them when you continue. Text already inserted into another application is never
automatically overwritten.

Shortcuts are configurable in Preferences, which also shows the actual recording
shortcut in its usage instructions. Click the tray icon for Preferences, or choose
**Quit** from its context menu to exit. Closing Preferences returns to the tray.
Optional **Start at Login** starts in the tray too. The microphone and
recording/loading badges show its state.

DictaDesk remembers the original X11 window and accessible editable field when
recording starts, including its caret or selection. If you change focus, further
text waits until you finish; DictaDesk then restores the original field, inserts
the remaining text and tries to return you to the application you were using.
If the original field disappears, its contents change, or it cannot be verified,
text is retained in **Last Transcript** and on the clipboard. Use **Copy Uninserted
Text** in the tray, or click the intended field and use the configured **Paste Last
Transcript** shortcut. No text is automatically sent to the newly focused field.

Local or remote
---------------

**Local:** audio is processed on your computer after the model download.
**Parakeet** is the default. Moonshine Tiny (English), Whisper Base and Whisper
Tiny are alternatives. Preferences includes a model guide, download sizes and
separate Download/Remove controls. Changing models or selecting Remote keeps
existing downloads. An installed model loads automatically when needed.

**Remote:** explicitly select OpenAI GPT Transcribe and supply an API key.
Audio is sent to OpenAI and API charges apply. Saved keys use the desktop
keyring. The connection test sends no audio and checks model visibility; it
does not verify transcription billing or quota. Remote mode sends bounded audio
snapshots, not a continuous Realtime stream.
A resumed phrase is transcribed again with its earlier context; repeated snapshots
can increase API usage. It is never an automatic fallback
when local recognition fails.

.. image:: media/screenshots/preferences-local.png
   :alt: Local Preferences with model selection, model guide, CPU and precision controls
   :width: 880

What it does
------------

* Dictation into other applications, with focus checks and recoverable text if
  insertion cannot continue safely.
* Spoken punctuation, including parentheses, brackets, braces and “dot dot dot”.
* Built-in DictaDesk name casing, plus a personal correction dictionary with
  an undo action. Personal rules take priority.
* Microphone selection and a short record/transcribe test.
* Configurable shortcuts, recording notifications, tray state icons and optional
  startup at login.
* Model download verification, separate model removal and idle unloading.

Current scope
-------------

Global shortcuts and automatic insertion currently require **X11**. Original-field
restoration uses AT-SPI2 accessibility. Applications that do not expose a usable
editable field (including some terminals, Electron apps and custom editors) use
clipboard recovery. Password fields are excluded. Wayland uses clipboard recovery
for tray recordings, without unsupported focus manipulation. Wayland,
other desktops, other distributions and ARM machines have not been validated.
GNOME needs a compatible tray extension for tray operation. Full screen-reader
acceptance and a fresh installation on a second machine remain outstanding.
The package has been checked in an isolated installation using this machine's
system libraries; that is not a fresh Mint VM test.

Recognition varies by voice, language and background noise. Parakeet performed
best in our small English tests; that is not a universal accuracy claim.
Moonshine Tiny is English-only. Whisper Tiny sometimes omitted or repeated text
in our tests. Long recordings are split for models with limited audio context.
Live insertion can pause if a model revises text already inserted. Check important
text before sending it. A live paid OpenAI transcription request is still unverified.

Recordings are temporary; recent transcription stays in memory unless exported.
There is no telemetry. Removing the Debian package also removes its downloaded
models for all accounts, but retains user settings, keyring entries and exported
transcripts. Upgrades retain models. Use Preferences to remove individual models.

Source and development
----------------------

Source uses a ``src/dictadesk`` layout. Build and test instructions are in
`DEVELOPMENT.rst <DEVELOPMENT.rst>`_. Installer binaries belong in GitHub Releases;
model weights, dependency wheels and local caches are not part of this repository.

.. image:: media/screenshots/about.png
   :alt: DictaDesk About screen with its microphone icon and version 1.2.0
   :width: 880

License and credits
-------------------

Copyright 2026 Erick Woods. Licensed under `GPL-3.0-only <LICENSE>`_.

Thanks to Python, GTK, libadwaita, PyGObject, ONNX Runtime, onnx-asr, NVIDIA
Parakeet, Moonshine, Whisper and the other libraries that make dictation possible.
Dependencies, models and supplied launcher artwork retain their own licenses.
See `NOTICE <NOTICE>`_ for acknowledgments and the artwork license.
