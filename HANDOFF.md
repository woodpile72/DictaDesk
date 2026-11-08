# DictaDesk — complete development handoff

**Read this file before changing anything.** It is intended to let a new coding session on the lake-house laptop resume without access to the previous conversation.

Written 2026-11-08. The project on the development computer is **`/home/erick/Projects/Open/DictaDesk`**. The capitalization is **DictaDesk**, not `dictadesk`; Linux paths are case-sensitive. The user plans to copy the whole project directory to the laptop.

## 1. What we are doing right now

We are developing and manually testing an **unpublished DictaDesk 1.2.0** on Linux Mint Cinnamon/X11. The user is testing installed Debian packages on a different computer, a laptop at the lake house. Remote debugging through pasted logs has been cumbersome, so development is moving onto that laptop.

**The immediate issue was wake listening doing nothing even though Test Microphone successfully transcribed `Computer.`. The user supplied a log that identified an actual gating stall. A fix has been implemented in code commit `767ee69` and tested with generated speech and simulated noise. Physical acceptance on the laptop is the next important step.**

Do not start over or redesign the app. Inspect the copied tree and existing functionality first. There has already been substantial implementation and regression testing.

### Current authorization and release status

- Keep version **1.2.0** for these unpublished test builds. Several different packages have that same version; a version string alone does not identify a build.
- **Do not publish a release, push changes, or install/restart the user's running app automatically.** The user explicitly requested a deb first, to copy/install/test on the laptop, then decide about publication.
- Last published release is **v1.0.12**, from 2026-11-04. Development is on local `main`, with unpublished commits after that release.
- Put each new verified deb in the user's home directory as **`~/dictadesk_1.2.0_amd64.deb`**, with a checksum sidecar.
- Put a Markdown guide with **all changes and usage since 1.0.12** beside it as **`~/dictadesk_1.2.0_changes-and-usage.md`**. Keep that guide current with each delivered build.
- The user earlier asked that public release notes/announcements and commit messages remain generic and not specifically promote the experimental command features. Subsequent requests explicitly require the comprehensive **local** changes/usage guide and this handoff to explain everything, including the experimental features. Preserve that distinction.
- A prior proposal to mute the microphone by voice was discarded. **Do not add mic mute/unmute commands.** A separate timed pause for voice listening exists instead.
- Do not send messages to other people or publish announcements.

## 2. Confirmed laptop bug and current fix

### Observations from the real laptop

The user reported:

1. Preferences → Command & Control said **Listening for “Computer”**.
2. Test Microphone transcribed “hello test test” correctly.
3. Test Microphone also transcribed a separately recorded **“Computer.”** correctly.
4. Saying the wake phrase during background listening produced no audible or visible acknowledgment.

The laptop log showed **every captured block classified above the fixed 0.015 RMS threshold**:

```text
rate: 16000
threshold: 0.015
voiced_samples == samples, repeatedly
aggregate rms: approximately 0.06 and higher
peak: 1.0
max_block_rms: up to approximately 0.92
clipped_samples: nonzero
handsfree.utterance elapsed: 166.869 seconds
handsfree.utterance voiced_blocks: 0
handsfree.utterance samples: 0
```

One background capture had 2,667,200 captured samples and the same 2,667,200 marked voiced in the aggregate meter. It was canceled only when the user started a microphone sample. There were no completed wake utterances to send to the recognizer during that wait. The separate sample then submitted 52,800 samples to transcription and successfully returned nine characters.

### Why this happened

`audio/handsfree.py::utterance()` previously required three consecutive 100 ms blocks below an **absolute** silence threshold before arming the wake capture. On this laptop, ambient input/boost/bias never went below 0.015. It therefore waited forever for silence and never reached speech recognition. The UI nevertheless continued saying it was listening for the wake phrase.

This is independent of transcription-model accuracy, wake-word spelling and Talk Back output. Lowering the old silence threshold would make this particular quiet-gap problem worse, not fix it. The logged input also clips at times; input gain/boost may still deserve a physical check, but the software must not require digital silence to function.

### Implemented fix in code commit `767ee69`

- Added a lightweight **AdaptiveGate** in `audio/handsfree.py`, used by the existing hands-free coordinator.
- It learns a rolling ambient floor outside a captured utterance. The configured threshold remains the minimum speech-start threshold.
- Speech start uses ambient floor × 1.6 + 0.003, bounded below by the configured threshold. Speech end uses ambient floor × 1.25 + 0.002, bounded below by 80% of the configured threshold. This hysteresis lets speech end against a noisy background.
- Gating measures **AC RMS**, subtracting the block's mean when measuring energy so a constant DC bias does not count as continuous speech. The actual captured PCM remains unchanged for the recognizer.
- Ambient learning is frozen during the utterance, so speech does not raise its own end boundary.
- Initial calibration uses a few blocks; the history holds up to 30 block levels, not audio.
- An incomplete/overlong capture is discarded and its calibration resets, allowing recovery if fan noise/automatic gain changes. No truncated command executes.
- The same existing microphone stream, in-memory buffers, local recognition worker, configured phrase and deterministic command registry are reused. There is no new wake engine/model or continuous cloud connection.
- Added metadata `handsfree.gate_ready` with noise floor, effective threshold and `energy_mode: ac_rms`; utterance metadata includes ambient/effective thresholds. Existing `audio.progress` statistics remain raw aggregate input measurements, so they may still show all raw blocks above the *configured* threshold even when the adaptive gate correctly regards the background as ambient.
- Preferences now reports calibration, waiting for a pause, speech detection, finishing capture, and the last recognition check. This contains no background transcript text.
- The tray tooltip can say it is calibrating/waiting instead of pretending to be ready.
- A successful wake match triggers a **Wake phrase heard** desktop notification and updates the last-check status **before destination tracking or audio acknowledgment**. Thus a failed/muted output device is distinguishable from a failed wake match. Notifications still respect the desktop's notification behavior/DND.
- The configured beep or optional spoken acknowledgment remains in place. Inline wake + command speech still skips the audible acknowledgment delay.

### Validation of this fix

- **307 unit tests passed** after the final changes.
- Ruff checks and source compilation passed.
- A focused regression reproduces the old “all blocks above threshold → never arm” stall, then verifies the adaptive capture can finish the speech.
- Constant ambient noise and isolated clicks do not submit speech for recognition.
- DC bias alone does not count as adaptive speech.
- Incomplete loud captures reset calibration and remain non-executable.
- A test verifies wake confirmation happens before target capture/beep and only once.
- **25 real local ASR trials passed** using generated speech, no physical microphone/playback/system action: default and custom Computer phrases, multiple callback offsets, reduced gain, isolated and combined commands, background RMS 0.04/0.06, DC offset 0.08, and negative phrases. This is not proof of all real-laptop conditions.
- GTK checks passed for explicit wake feedback/activity, optional feature setup, ordinary dictation recovery, Talk Back, and Help.
- See `.qa/current/noise-unit-final.log`, `noise-real-wake.log`, `noise-voice-ui-final.log`, `noise-setup-ui-final.log` and the package logs described below. These ignored files exist if the whole directory was copied.

**Physical laptop validation is still outstanding.** Do not claim that generated tests establish the user's microphone or speakers have passed.

## 3. What to do first after copying this directory

1. Read this handoff and inspect `git status --short` and `git log -5 --oneline`. Do not discard local edits from the transfer or replace this tree with a fresh checkout of the older public repository.
2. Check the actual project path/case on the laptop. The development path was `~/Projects/Open/DictaDesk`.
3. Determine whether the laptop is running the installed app or a source copy. **A copied source tree does not update `/usr/lib/dictadesk` or an already running process.**
4. The current noise-gate fix is in the code even if the laptop still runs an older 1.2.0 package. Install the latest transferred deb manually after quitting the old app, or run the current source with a fresh environment. Do not run both instances during a microphone test.
5. Reproduce the original scenario with the actual default microphone and ordinary gain: enable/save Command & Control, leave a short quiet/background interval, say **Computer**, wait for the beep/Hello and notification, then say a harmless command such as **volume down five percent** or **open browser**. Also test **Computer open browser** in one utterance. The user can choose when to perform real microphone/desktop actions.
6. Watch the new local-listening activity and logs. Check for `handsfree.gate_ready`, then `handsfree.utterance`, `handsfree.recognizing`, `handsfree.wake_result`, and `handsfree.command_result` in that order.
7. Test repeated activations, return to listening, ordinary/extended dictation, Escape, and the real speaker acknowledgment. Preserve session generation guards and microphone cleanup if further fixes are needed.

Useful commands, run from the project root:

```bash
git status --short
git log -5 --oneline
ps -eo pid,lstart,args | rg '/usr/bin/dictadesk|dictadesk.audio.worker|dictadesk.ui.application'
dpkg-query -W dictadesk
tail -n 100 ~/.local/state/dictadesk/diagnostics.log
sha256sum src/dictadesk/audio/handsfree.py /usr/lib/dictadesk/dictadesk/audio/handsfree.py
```

Matching installed/source Python file hashes are a useful check because **every test package still says 1.2.0**. An already running process must also be restarted after an upgrade to use the new code.

### Interpreting the next log

- No input callbacks: device/stream problem. Existing five-second callback watchdog should report it.
- Calibration but no actual speech detections: inspect gain/background levels and effective threshold, and verify the saved microphone/model match the intended choices.
- Completed utterance but no wake recognition submission: inspect generation cancellation and the coordinator state.
- `wake_result matched: false`: captured speech reached ASR but did not match. The normal log intentionally records character counts, not recognized text. A deliberate microphone sample can check exactly what the model returns.
- `wake_result matched: true`, then no audible acknowledgment: check the explicit wake notification/last-check indicator, target-capture delay, Talk Back readiness, beep setting and default output device. This is a different stage from microphone capture.
- `command_result recognized: false`: wake worked; command recognition failed. Never fall back to shell execution.
- Desktop lock unknown/locked: commands are deliberately paused. Do not bypass the lock safety guard to make a test appear to work.

The user has already confirmed that a deliberate microphone sample transcribes Computer correctly. Do not send them through generic microphone-selection advice again without new evidence.

## 4. Features already implemented — preserve them

### Tray-only lifecycle

- No main application window is created at startup or during normal use.
- Tray icon/status/menu, Preferences, optional login startup, global shortcuts and notifications remain.
- Preferences is standalone; closing it returns to tray operation. Help has no recording controls or transcript editor.
- Main-window code and obsolete tests were removed, including `ui/window.py`.
- Icon geometry is constant across states: compact microphone, same canvas, small badges, consistent symbolic/color family, no timed flashing. **Do not reintroduce pulsing/size changes.**

### Dictation shortcuts and session safety

| Default shortcut | Actual function |
|---|---|
| Super+V | Standard dictation; press again or Escape to finish. Inserts completed text at pauses. |
| Super+Shift+V | Extended/continuous dictation; press again or Escape to finish. Local Live Insertion can insert stable words while speaking. |
| Super+Shift+X | Cancel recording. |
| Super+Shift+P | Explicitly paste last completed dictation into a verified chosen field. |

All are configurable. Help uses the configured values. **Super+Shift+V is NOT a system-command shortcut.** An earlier giant request proposed that mode but was explicitly replaced. Do not implement that superseded design.

Standard dictation has configurable silence stopping (20 seconds default; 0 means Off), text-update pauses, sentence-ending pauses (4 seconds default) and a maximum recording duration. Extended dictation uses a session-length limit without standard silence stopping.

Commands said during normal/extended dictation must never execute system actions.

### Original destination and clipboard fallback

- The recording session owns the original X11 window and accessible editable-field destination, captured at recording start, not transcription completion.
- Native X11 and AT-SPI2 track field identity, content context, caret and selection where available.
- When focus changes, further insertion waits; completion attempts to restore/verify the original field, insert there and return focus to the later app when possible.
- Stale fields, page navigation, changed contents, removed/closed windows and unsupported controls fail safely. Password fields are excluded.
- Insertion failure copies valid recognized text to the clipboard, shows a short nonblocking notice and immediately allows another recording.
- **There is no Last Transcript window/menu, pending transcript queue, manual clear/recover workflow or acknowledgment gate. Do not bring one back.**
- Empty/invalid recognition must not overwrite the clipboard.
- `last_dictation` is an in-memory completed dictation for explicit copy/paste utilities. It is not a pending-insertion gate. Commands do not overwrite it.
- Successful, failed, canceled, empty and timed-out operations release resources and return to usable state. Old results cannot contaminate the next recording.

### Command & Control

- Optional, experimental, local-only, off by default. Background capture stays inactive when disabled. Dictation always has microphone priority.
- **Computer** is the new default. Old Clanker/Hey Clanker default settings migrate on read. Other custom phrases survive.
- `SettingsStore.save()` writes a root `wake_phrase_default` marker. This prevents repeated migration if someone deliberately chooses Clanker again after upgrading. The preferences schema remains 1.
- **Two-step:** say configured wake phrase → acknowledgment beep or optional spoken Hello → separately speak a command → silence completion → action → resume.
- **Inline:** say **Computer lock screen** or **Computer volume down fifteen percent**, then pause. A completed recognized command executes without waiting for an audible acknowledgment. Visual wake confirmation still occurs.
- Exact configured wake matching for inline speech; narrowly bounded tolerance for an isolated wake phrase. Do not loosen system-command grammar with fuzzy arbitrary execution.
- Existing 200 ms pre-roll preserves weak onsets. Complete utterances are required; truncated commands do not execute.
- Activation captures the target window when the wake phrase is recognized. Window actions and window screenshots use that identity, not whichever window is focused at execution. Safe paste also uses the original editable target.
- Generations/epochs invalidate canceled, disabled, locked, superseded and stale chooser callbacks. Do not remove these guards.
- Capture closes before ASR/acknowledgment. Recognition and timeouts are bounded. The new adaptive gate fixes an amplitude-silence wait; it does not add another recognizer or cloud service.
- Voice listening can be paused for 30 minutes by default, resumed early from tray, and paused by supported spoken durations. This does not mute the microphone system-wide or block dictation.
- Desktop lock detection pauses commands; a confirmed lock cancels capture/dictation and stale actions. Unlock can resume enabled listening, never canceled dictation.

### Command registry and desktop integrations

Definitions live centrally in `core/commands.py`; categories, phrases, parameters, descriptions, examples, handlers and capabilities feed the Help reference. Do not add an unrelated hardcoded command list in the GUI.

Implemented categories include:

- Audio: relative volume up/down (5 percentage points default), optional numeric/spoken amounts, absolute volume, max, mute/unmute speakers. Default step configurable. **No mic mute/unmute.**
- Display/power: brightness up/down/set, supported backlight detection, lock, screensaver, screen off, suspend, DND on/off. Normal desktop authorization; no root app and no silent substitute actions.
- Media: MPRIS play, pause, toggle, next, previous, stop, with missing-player handling.
- Screenshots: desktop tool full-screen and remembered-window capture.
- Windows: show desktop, switch relative to remembered window, maximize/minimize, fit left/right monitor work area, request normal close. Never kill processes or bypass unsaved-work prompts.
- Applications: preferred browser/files/mail/terminal/calculator/editor/media/viewer/office roles, plus specific installed apps by display name, stable desktop ID or executable name.
- Clipboard: copy last completed dictation, safe paste, clear clipboard without erasing last dictation.
- Dictation formatting: paragraph, line, tab and punctuation/brackets/braces, only when Spoken Punctuation is enabled. These are not system commands.

Generic **open browser** and **open file manager** resolve live Cinnamon/default-app preferences. Specific **open Nemo** works even if its menu name is Files and a different file manager is preferred. Specific **open Firefox** remains Firefox. Ambiguous/approximate names require a chooser, not an arbitrary launch guess. Speech is never passed to a shell.

X11/Cinnamon is the primary target. Wayland degrades safely: no unsupported focus/global-shortcut/window manipulation. Brightness requires supported desktop/backlight interfaces. External-monitor DDC is not implemented. DND, screenshot and media support depend on installed desktop facilities. Unsupported actions must report failure, not success.

### Preferences, guided setup and Help

Preferences has **top tabs**:

1. **Dictation** — opens first, holds primary microphone/model/keyboard/timing/text/general settings.
2. **Command & Control** — red Experimental banner, setup requirement near top, enable/wake/ack/status/help, with volume/brightness adjustments at the bottom.
3. **Talk Back** — same big red Experimental banner, prominent setup requirement, optional speech engine/voice/phrase/preview/removal settings.

Enabling a feature with missing files opens a **three-screen modal setup flow** in `ui/feature_setup.py`:

1. Choose Engine.
2. Choose Model/Voice (precision where appropriate).
3. Download/Install the required files.

The actual download action has its own screen and is not buried below other settings. Next/Back navigate, Cancel leaves the feature off. Enable is unavailable until files are ready. Existing downloads are reused. The final Enable updates draft choices; **Save Preferences applies them**. C&C setup can explicitly adopt Local transcription/model shared with dictation. Do not create a separate competing C&C recognition model system.

A missing dictation model also has a prominent top setup section and an explicit prompt. That prompt is restricted to the Dictation page so it does not interrupt independent Talk Back setup.

Test Microphone uses current **unsaved** microphone/model choices. Changing an option permits another sample. Model loading/retesting and explicit download were fixed; preserve them.

The tray has separate **Dictation Help**, **Command & Control Help**, and **Talk Back Help** entry points. They select pages of a single standalone scrollable Help window; reopening focuses it. Its title is **DictaDesk Help**. Close button, title close and Escape work independently of Preferences. Tables are registry-generated; configured shortcuts/wake phrase/increments/ACK are dynamic.

### Talk Back — optional local TTS

- Off by default; default spoken wake acknowledgment **Hello**, editable up to 120 printable characters.
- Provides acknowledgment for enabled Command & Control; it is not a general document reader.
- Piper natural voices: Lessac medium (~63 MB), Lessac high (~114 MB), Amy low (~63 MB), plus ~34 MB shared optional engine. First medium setup is about 97 MB downloaded. Installed size is larger.
- eSpeak NG system voices: English US and UK. UK maps to eSpeak's `en` voice identifier internally. Uses the system `espeak-ng` package rather than a Piper download.
- Piper engine 1.8.0/pathvalidate 3.3.1 and voices are pinned by version/revision/size/SHA256. Optional wheels extract privately; no runtime pip/shell execution and no heavyweight new baseline dependency.
- Engine/voices download only on explicit action. Temporary staging is cleaned on cancel/failure. No voice weights are bundled in the deb.
- eSpeak now has **Install eSpeak NG…**, invoking fixed `apt:espeak-ng` through the desktop `apturl` installer with its normal confirmation/authentication. DictaDesk is never run as root.
- **Check Again** invalidates cached availability and detects an installed system engine without restarting. On desktops without apturl, the UI directs installation through the normal system package manager, then Check Again.
- This fixes the user's earlier “switched Piper → eSpeak, removed downloads, now stuck” workflow. Setup requirements take priority over stale removal/preview success messages. Removing TTS downloads leaves a system-installed eSpeak engine usable.
- Test Voice uses draft settings without saving. Stop Preview/close/starting dictation cancels output; recording waits for preview output to stop.
- Speech uses a separate isolated local synthesis worker and playback service. It must not interfere with the ASR worker or microphone ownership. Background voice downloads do not set the main recording session busy.
- Synthesis failure falls back to the configured beep and cannot disable dictation.
- Remove TTS Downloads removes only optional speech files. Remove Downloaded Models removes all app model downloads including TTS, with explicit confirmation and conflict checks.

## 5. Architecture and relevant files

```text
Standard/extended dictation
  global shortcuts -> core/session.py -> audio Capture/continuous
  -> ASR subprocess -> transcript postprocessing
  -> session-bound desktop destination -> safe insertion / clipboard fallback

Command & Control
  core/handsfree.py -> MemoryInput -> AdaptiveGate + utterance segmentation
  -> same local ASR worker -> configured wake matching
  -> strict central command registry -> desktop handlers
  -> return to listening, with generations protecting async callbacks

Talk Back
  Preferences/catalog -> verified optional download store
  -> independent synthesis subprocess -> cancellable PCM playback
  -> beep fallback on output failure
```

| Area | Files |
|---|---|
| App lifecycle/actions/notifications/tray delivery | `src/dictadesk/ui/application.py` |
| Primary operations, cleanup, recovery | `core/session.py` |
| Capture/levels and extended dictation | `audio/input.py`, `audio/continuous.py` |
| Worker boundary and actual ASR | `audio/service.py`, `audio/worker.py` |
| Wake audio, noise gate, pre-roll, beep | `audio/handsfree.py` |
| Wake lifecycle/generations/feedback | `core/handsfree.py` |
| Wake parser/application name matching | `core/voice_commands.py` |
| Registry and parameters | `core/commands.py` |
| Default application roles/resolution | `core/application_roles.py`, `desktop/applications.py` |
| Desktop actions and window requests | `desktop/commands.py`, `desktop/window_commands.py` |
| Original field/window tracking | `desktop/destination.py`, `desktop/accessibility.py`, `desktop/delivery.py` |
| Session lock awareness | `desktop/lock.py` |
| Preferences and guided enable | `ui/preferences.py`, `ui/speech_preferences.py`, `ui/feature_setup.py` |
| Help/reference | `ui/usage.py` |
| TTS catalog/downloads/output | `core/tts.py`, `audio/speech.py`, `audio/tts_worker.py` |
| Validated settings/migrations | `core/settings.py` |
| Private metadata logs | `core/diagnostics.py` |
| Tray DBus menu/icons | `desktop/tray.py`, `resources/dictadesk-panel-*` |

Paths in the table are relative to `src/dictadesk/` unless fully prefixed. GUI work is dispatched through GLib; blocking/native work stays in workers. Reuse existing helpers instead of adding parallel lifecycle implementations.

## 6. Environment, cache and transfer details

- Project: Python **3.12**, GTK4 **4.14+**, libadwaita **1.5+**, Linux Mint Cinnamon/X11, amd64.
- Runtime dependencies are fixed in `requirements.lock`; packaging vendors those wheels privately under `/usr/lib/dictadesk/vendor`.
- Source layout: `src/dictadesk`; installed entrypoint `/usr/bin/dictadesk`; installed application code `/usr/lib/dictadesk/dictadesk`.
- Packaged model/voice cache: `/var/cache/dictadesk/<uid>/`; source-run models: `~/.cache/dictadesk/models/`. TTS lives under a private `tts` subdirectory. Different UIDs on a new computer get their own cache.
- Preferences: `~/.config/dictadesk/preferences.json` (or XDG config). Credentials use the desktop keyring; do not dump/export keys.
- Diagnostics: `~/.local/state/dictadesk/diagnostics.log` (or XDG state), 512 KiB rotation with two backups, mode 0600. No raw audio, transcripts or API keys.
- Temporary recognition workspaces are removed after operations. Do not add permanent raw mic recordings merely to debug.
- Package upgrades preserve downloads/settings. Removing/purging the package removes its dedicated downloaded-model cache for all users, but not user preferences/keyring entries.

The development folder was about **6.1 GB**, largely ignored test/build caches, repeated exported builds and environments. Copying the whole folder works, but this is much larger than the source itself. `.git`, `src`, `tests`, `scripts`, `requirements.lock`, packaging/data/docs and this handoff are the important working materials. `.build`, `.qa`, `dist`, `.venv`, `__pycache__` and `.ruff_cache` are ignored development artifacts. Keeping `.qa` preserves test logs and downloaded test models but is not required to run the installed app.

**Do not assume a copied virtual environment is portable.** Recreate it on the laptop using the distro's Python 3.12 and system GI/Cairo libraries. Creating `--clear` below clears the copied `.venv`, not application preferences/models or source:

```bash
cd ~/Projects/Open/DictaDesk
python3 -m venv --clear --system-site-packages .venv
.venv/bin/python -m pip install --require-hashes -r requirements.lock
.venv/bin/python -m pip install --no-deps -e .
.venv/bin/python -m pip install ruff onnx
```

See `DEVELOPMENT.rst` for distribution dependencies. GTK/Adwaita/PyGObject/Cairo, PortAudio, libsndfile, AT-SPI and PulseAudio utilities must come from the distro. The baseline lock targets amd64; do not silently regenerate it for another platform. Root is only for normal package-manager installation, never for running DictaDesk.

For source launch, first quit the installed tray instance so it does not retain the app ID/microphone:

```bash
.venv/bin/dictadesk
```

No new source checkout from public `main` is needed: the copied local repository contains unpublished work that public 1.0.12 does not.

## 7. Tests and exact workflows

### Headless unit checks

```bash
.venv/bin/python -m unittest discover -s tests
.venv/bin/ruff check src tests scripts
.venv/bin/python -m compileall -q src
```

Expected current unit count: **307**. Unit tests mock audio/desktop effects; they do not use the real microphone, download models or make paid API calls.

### Isolated GTK/X11 checks

Use **`scripts/test-ui`**, which creates private HOME/XDG directories, Xvfb and a private session bus. **Do not run integration scripts directly on the user's real desktop.** They include synthetic desktop controls and clipboard/window fixtures.

```bash
scripts/test-ui .venv/bin/python tests/voice_commands_acceptance.py
scripts/test-ui .venv/bin/python tests/feature_setup_acceptance.py
scripts/test-ui .venv/bin/python tests/talkback_acceptance.py
scripts/test-ui .venv/bin/python tests/usage_acceptance.py
scripts/test-ui .venv/bin/python tests/preferences_window_acceptance.py
scripts/test-ui .venv/bin/python tests/ux_fixes_acceptance.py
scripts/test-ui .venv/bin/python tests/tray_acceptance.py
scripts/test-ui .venv/bin/python tests/recovery_acceptance.py
scripts/test-ui .venv/bin/python tests/continuous_acceptance.py
scripts/test-ui .venv/bin/python tests/destination_acceptance.py
scripts/test-ui .venv/bin/python tests/firefox_destination_acceptance.py
scripts/test-ui .venv/bin/python tests/lock_acceptance.py
scripts/test-ui .venv/bin/python tests/system_commands_acceptance.py
scripts/test-ui .venv/bin/python tests/application_defaults_acceptance.py
```

Small-display example:

```bash
DICTADESK_TEST_SCREEN=1024x600x24 scripts/test-ui .venv/bin/python tests/feature_setup_acceptance.py
```

Portal/PipeWire/DRI warnings are common on the isolated Xvfb bus; assess test assertions/exit codes rather than treating every desktop warning as an app regression.

### Generated real-ASR wake regression

If the copied test cache exists:

```bash
.venv/bin/python tests/real_wake.py --model-cache .qa/real-speech/models
```

This uses generated libespeak-ng/FFmpeg speech and the real local Parakeet model, while replacing microphone, playback and launches. It does **not** run system actions or prove physical mic performance. It now includes additive background noise and DC-offset scenarios that were missing from the earlier quiet-fixture tests. If the cache was not transferred, obtain a test model through the existing opt-in model workflow; do not mistake absent QA weights for a source bug.

### Building a deb

Build from a clean git export after tests, not a mixed working directory. `scripts/build-deb` invokes `scripts/build_deb.py`. `pyproject.toml` and `src/dictadesk/__init__.py` must both remain 1.2.0.

```bash
DICTADESK_PYTHON=/absolute/project/.venv/bin/python scripts/build-deb
```

The builder uses hash-locked wheels and emits `dist/dictadesk_1.2.0_amd64.deb`, its `.sha256`, and `SHA256SUMS`. It includes no downloaded recognition/voice weights; dependency wheels include their upstream processing graphs/fixtures. Piper is optional, downloaded later, not a new baseline requirement.

If the copied wheelhouse is available, an exported build can be offline:

```bash
DICTADESK_PYTHON=/absolute/project/.venv/bin/python \
PIP_NO_INDEX=1 PIP_FIND_LINKS=/absolute/project/.build/wheels \
/absolute/export/scripts/build-deb
```

Some build logs show pip complaints about unrelated system scipy/types packages while installing the private target. Check the final exit code and installed-package tests; do not alter the reviewed runtime lock merely to silence unrelated system-site warnings.

### Installed-package validation

The previous agent extracted a deb under `.qa/package-...`, then used root-created `bwrap` only as a mount sandbox. The actual test process runs as the normal user with a private HOME/bus/Xvfb and **network disabled**. It binds the extracted package at `/usr/lib/dictadesk`, launcher at `/usr/bin/dictadesk`, and its root-owned sticky model cache at `/var/cache/dictadesk`. This tests installed paths without replacing the user's real installed package.

Look at `.qa/current/run-setup-package` and the later noise-package wrapper if the ignored QA tree was copied. Important details:

- Top `/var/cache/dictadesk` must have root ownership and package-created sticky permissions. Ordinary-user namespace mapping can otherwise make the model-cache safety check fail.
- `PYTHONPATH=/usr/lib/dictadesk:/usr/lib/dictadesk/vendor` selects extracted code for test scripts; actual launcher/worker bootstrap paths are tested too.
- `DICTADESK_PACKAGE_TEST=1` is required by `tests/package_smoke.py`.
- Test as the real normal account, not mapped UID 0. Adapt the `runuser` account if the laptop username differs.
- Do not bind the real user's model cache or session bus into these isolated integration tests.
- Compare all packaged `src/dictadesk` source/resource bytes with the frozen export before copying a deb to the user's home.

Manual installation for the user:

```bash
# Quit the running DictaDesk using its tray menu first.
sudo apt install --reinstall ~/dictadesk_1.2.0_amd64.deb
# Relaunch from the application menu afterward.
```

Do not publish after this merely because automated checks pass. The user wants to test first.

## 8. Recent history and what it explains

All listed commits are local unpublished work after v1.0.12:

| Commit | Purpose |
|---|---|
| `c8f9b65` | Tray-only 1.2.0, original destination tracking, initial integrations/reference. |
| `4a8d1a5` | Handle unavailable/stale desktop accessibility at startup. |
| `68776bf` | Wait for native device checks before PortAudio/interpreter shutdown. |
| `beade14` | Preferences/model prompt/help closing/microphone retest fixes. |
| `f4ca378` | Remove blocking transcript recovery and harden recording cleanup. |
| `cf09d55` | Preserve short onsets and recognition feedback. |
| `9dce75e` | Stable tray icon geometry/rendering. |
| `adc7984` | Desktop default-app roles and stable app identifiers such as Nemo. |
| `9ac0025` | Three Preferences tabs, optional local speech output, split Help and inline commands. |
| `fac1f76` | Computer default/migration and matching red Experimental banners. |
| `049f850` | Guided setup and explicit missing-file/eSpeak install/recheck UX. |
| `767ee69` | Ambient-aware/AC wake gate and explicit activation feedback, current bug fix. |

A documentation-only handoff commit may follow `767ee69`. That does not change the code used by the noise-fix deb. Use `git log` and the checksum to identify the actual transferred state.

## 9. Remaining acceptance checklist on the laptop

- [ ] New code is actually running, not a stale same-version process/package.
- [ ] Ambient calibration completes and Computer is recognized against the actual background.
- [ ] Both isolated wake → ACK → command and inline Computer + command work repeatedly.
- [ ] The beep/Hello comes from the expected physical output and does not feed back into capture.
- [ ] A wake notification/last-check indication distinguishes detection from output failures.
- [ ] Standard/extended dictation continue to work, including Escape and repeated sessions.
- [ ] Field/window tracking across apps, browser navigation and disappearing targets remains safe.
- [ ] Failed insertion copies valid text to clipboard and immediately permits the next recording.
- [ ] Guided setup/cancel/retry works with real downloads and the actual eSpeak desktop installer.
- [ ] Preferences/Help close properly; tray icon sizing remains stable on the laptop panel.
- [ ] User explicitly approves publication after testing. No automatic release.

Real screenshots, hardware brightness/power behavior, fresh-Mint dependency installation, Wayland acceptance, screen-reader acceptance and paid live remote transcription have not all been validated. Do not convert private/mock integration passes into broader platform claims.

## 10. Complete changes and usage reference

The following section embeds the full local build guide so this file remains useful even if the separate Markdown beside the deb is not copied. It includes command tables generated from the registry. The GUI remains data-driven; this handoff is a snapshot for transfer.

# DictaDesk 1.2.0 — changes and usage

Local testing build · 2026-11-08 · source `767ee69dcb823311a9f14daac789de5cb50e3b75`

This guide covers the changes since the last published release, **1.0.12**, including the optional experimental features. This 1.2.0 build has not been published; laptop installation and testing come first.

## Files and installation

- Package: `dictadesk_1.2.0_amd64.deb` (54,289,132 bytes, about 54.3 MB).
- Package checksum: `2e3fae271b93262ff9c52f1d8b26e5315252f24d3dab30feb8979d1187458998`.
- Companion guide: `dictadesk_1.2.0_changes-and-usage.md`.
- Target: Linux Mint Cinnamon/X11, amd64, Python 3.12, GTK 4.14+ and libadwaita 1.5+.

Copy the package to the laptop, quit the running DictaDesk from its tray menu, then install it:

```bash
sudo apt install --reinstall ./dictadesk_1.2.0_amd64.deb
```

Launch DictaDesk again from the application menu. The version is still 1.2.0, so the checksum distinguishes this build from earlier test packages. Existing preferences, downloaded dictation models and keyring credentials are preserved by upgrades. No automatic installation or publication was performed.

## Changes since 1.0.12

### Tray-only operation and Preferences

- Removed the main application window, its recording controls and its transcript editor. Startup and ordinary use remain in the tray.
- Preferences is a separate window. Closing it leaves DictaDesk running.
- **Three tabs across the top of Preferences:** **Dictation**, **Command & Control**, and **Talk Back**. Dictation opens first and contains the primary recording, microphone, model, timing, keyboard, text and general settings. Each tab scrolls independently.
- Experimental Command & Control and Talk Back settings have their own tabs, each with the same prominent red Experimental banner. The hands-free enable switch and wake phrase come before volume/brightness adjustments; those adjustments stay at the bottom of Command & Control.
- Missing local speech models have a prominent setup section and an explicit download prompt. Model selection, descriptions and download/removal controls remain available. Command & Control and Talk Back show Setup Required at the top when files are missing.
- Test Microphone uses the options currently selected in Preferences, including unsaved microphone/model changes. Changing an option permits another sample; selected models can load automatically for the test.
- Help is split into **Dictation Help**, **Command & Control Help**, and **Talk Back Help**, available from the tray and the relevant Preferences tab. They select separate pages of one reference window. Reopening focuses the existing window.
- Help closes with its Close button, title-bar close button or Escape, including while Preferences is open. Help has no recording controls or transcript editor.
- Panel icons use the same microphone silhouette and canvas in every state. Small status badges and consistent symbolic/color variants avoid the previous apparent size changes and pulsing.

### Reliable destination tracking and failure recovery

- Each recording binds its destination before asynchronous recognition starts. Native X11 APIs remember the original window; AT-SPI2 remembers an accessible editable field, caret/selection and content context where supported.
- With unchanged focus, existing insertion continues normally. When focus changes, remaining text waits until completion, then DictaDesk attempts to restore and verify the original field, insert there and return focus to the application used afterward.
- A window-level fallback is allowed only when its intended insertion target can be verified. Closed windows, stale accessibility objects, changed pages/contents and unrelated focused fields fail safely. Password fields are excluded.
- Failed insertion copies valid recognized text to the clipboard, gives a brief nonblocking notification and returns to Ready. Empty or invalid recognition does not overwrite the clipboard.
- Removed Last Transcript, manual clearing, pending transcript queues and acknowledgment gates. A previous transcript or insertion failure cannot prevent the next recording.
- The last completed dictation remains in memory for explicit copy/paste utilities. Command recognition never replaces it.
- Recording, cancellation, empty recognition, inference errors, insertion acknowledgment and shutdown release resources and settle back to usable state. Worker communication, inference, cancellation and delivery waits are bounded; native device checks finish before audio shutdown.
- Missing desktop accessibility is reported without preventing tray startup. Where automatic insertion is unavailable, recognized text remains available on the clipboard.

### Recognition and diagnostic improvements

- Wake capture retains approximately 200 ms of pre-roll so callback alignment does not drop the start of a short wake phrase.
- Fixed a confirmed laptop stall: every block exceeded the fixed 0.015 RMS threshold, so the listener waited 166 seconds for absolute silence and submitted no wake audio, despite microphone-test transcription working. The hands-free gate now learns ambient noise, uses AC RMS so DC bias is not speech, and applies separate speech-start/end thresholds. It recalibrates after incomplete loud captures. Normal dictation settings are preserved.
- Local-listening status now shows calibration, pause waiting, speech capture and the last recognition check. A wake-match notification occurs before destination tracking or audible acknowledgment, separating detection from output problems.
- Wake matching tolerates narrowly bounded recognition errors in an isolated wake phrase. Combined wake-plus-command speech requires the configured phrase itself; system commands retain exact deterministic matching.
- Completed utterances are required for command execution. Audio cut off by its duration limit, deadline or stream termination does not execute a partial command.
- Diagnostics include captured sample counts, RMS/peak levels, threshold decisions, worker timing/errors and state/resource transitions. A responding microphone meter alone does not prove speech exceeded the RMS silence threshold or reached a working recognizer.
- Private rotating diagnostic logs are at `~/.local/state/dictadesk/diagnostics.log` (or the configured XDG state location). They contain metadata, not transcript text, raw microphone recordings or API keys. Temporary recognition audio is cleaned up.

### Optional hands-free controls and application names

- Command & Control is optional, experimental and **off by default**. Enable it on its Preferences tab with Local transcription selected and a local model downloaded.
- New profiles default to **Computer**. Old Clanker and Hey Clanker default settings migrate to Computer when read on upgrade. Other custom phrases are preserved. Saving records the migration, so you can still deliberately choose an old phrase later.
- Supports both wake-only → acknowledgment → separate command and a single utterance such as **“Computer lock screen”**. The latter executes a complete recognized command without an acknowledgment delay.
- Commands use the central registry below, with case/punctuation normalization and numeric or spoken percentages. No arbitrary spoken shell execution is used. Unknown commands report failure without a system action.
- Screen/window commands capture their target when the wake phrase is recognized. Window close uses the normal window-manager close request, preserving applications’ unsaved-work prompts.
- Common application names follow the desktop's Preferred Applications: “open browser”, “open file manager”, “open terminal”, and the other roles below. Changes to defaults are resolved when the command runs.
- Specific installed applications also match display names, desktop-entry IDs and executable names. **“Open Nemo”** resolves Nemo even when the menu calls it **Files**, and remains specific to Nemo when another file manager is preferred. “Open Firefox” remains specific to Firefox.
- Ambiguous/approximate application names require selection instead of silently launching a guess. Stale chooser callbacks are invalidated.
- Pause Voice Commands in the tray pauses listening for 30 minutes. It resumes automatically, or use Resume Voice Commands Now. Supported spoken pause durations also appear in the registry.
- Desktop lock monitoring pauses unattended commands; confirmed locking cancels active dictation/capture and invalidates pending actions. Unlock can resume enabled listening, without restarting canceled dictation.

### Talk Back — Experimental

- Optional local speech output supplies a spoken wake acknowledgment. **Off by default**; the editable default phrase is **Hello**.
- Choose **Piper** for downloaded natural voices, or installed **eSpeak NG** for a small system voice. Download sizes are listed below. No optional engine or voice is included in the deb.
- Downloads happen only after an explicit Download action, use pinned sizes/hashes, and publish complete files. Cancellation/failure discards temporary files. Synthesis uses a separate local worker and does not send text/audio to a service.
- Test Voice previews unsaved engine, voice and phrase choices. Stop Preview, closing Preferences or starting a dictation operation interrupts the preview; microphone recording waits for its output to stop.
- Enabling a feature with missing files opens a separate three-step setup dialog: Engine → Model/Voice → Download/Install. The download action has its own screen and cannot be missed below other settings. Enable stays unavailable until the files are ready; Cancel leaves the feature off. Downloaded files are reused. Save Preferences applies the completed setup.
- eSpeak NG has an explicit Install eSpeak NG button, using the desktop APT installer with its normal confirmation/authentication. Check Again detects installation without restarting. Other desktops can install espeak-ng through their package manager and Check Again. Removing TTS downloads leaves a system-installed eSpeak engine intact. Stale removal/preview notices no longer hide missing-file instructions.
- Background downloads leave dictation available. If speech output fails, the configured acknowledgment beep is the fallback. Experimental speech failures do not disable ordinary dictation.
- Remove TTS Downloads removes optional engine/voice files independently of dictation models. The existing Remove Downloaded Models operation removes all application model downloads, including optional speech files, with an explicit confirmation.

## Dictation usage

These are defaults; configured shortcuts are shown in Preferences and Help.

| Shortcut | Action |
|---|---|
| Super+V | Start standard dictation; press again to finish. Text inserts at pauses. |
| Super+Shift+V | Start extended/continuous dictation; press again to finish. Local Live Insertion can insert stable words while speaking. |
| Escape | Finish standard/extended dictation; cancel an active voice command. |
| Super+Shift+X | Cancel recording. Text already inserted into another application stays there. |
| Super+Shift+P | Explicitly paste the last completed dictation into a verified field you choose. |

1. Open Preferences → Dictation, select a microphone and download a local recognition model if needed. Test Microphone can check input and recognition.
2. Click the text field you want to dictate into.
3. Press the standard or extended shortcut and speak.
4. Press the same shortcut again, or Escape, to finish. If you switched focus, remaining text returns to the original field when it can be safely verified.
5. If insertion fails, paste the clipboard text yourself. You can start the next recording immediately.

Standard dictation retains the configurable silence stop (20 seconds by default; 0 means Off). Sentence endings use the existing configurable pause (4 seconds by default). Extended dictation uses its configured session limit without the standard silence stop. Explicit punctuation and personal vocabulary corrections remain available. These existing workflows were preserved; Super+Shift+V is **extended dictation**, not a system-command shortcut.

Local transcription stays local after its one-time model download. Optional Remote transcription still requires explicitly selecting it and providing an API key; audio then goes to that provider and charges may apply. Remote mode is not an automatic fallback.

## Command & Control usage

1. Open Command & Control and turn on hands-free commands.
2. If required files are missing, follow Engine → Model → Download in the setup dialog, then Enable. Local recognition is selected and its model is shared with dictation. Choose your wake phrase and Save Preferences.
3. Use either form:
   - **One phrase:** “Computer volume down fifteen percent”, then pause. One recognized command executes directly.
   - **Two steps:** “Computer”, wait for the acknowledgment, then say “volume down fifteen percent”.
4. DictaDesk resumes listening after the action. Escape cancels an active command. Manual dictation takes priority and never executes system commands.

Substitute your configured phrase for Computer. Listening is continuous only while enabled, unpaused and permitted by desktop lock status. It uses the selected local recognition model and microphone. If recognition misses quiet speech, test the microphone and adjust Silence Threshold on the Dictation tab; do not assume a moving peak meter proves adequate RMS speech levels.

Relative and absolute values differ: “volume up fifteen percent” adds **15 percentage points** to the current value; “volume fifteen percent” sets the value to **15%**. Relative output levels are clamped to 0–100%. Default volume and brightness increments are each 5 percentage points and are configurable.

## Command reference

The following tables are generated from this build's actual command registry, using default increments. The GUI uses saved increments and live availability checks. Desktop-specific commands may be unavailable; that is reported instead of pretending success. Dictation Formatting applies to dictation with Spoken Punctuation enabled, without a wake phrase.

### Audio

| Say | Action |
|---|---|
| Volume up<br>Volume up fifteen percent | Increase by 5 percentage points by default, or by the spoken amount. |
| Volume down<br>Volume down 15% | Decrease by 5 percentage points by default, or by the spoken amount. |
| volume max | Set output volume to 100%. |
| Volume 50% | Set absolute output volume to the spoken percentage (0–100%). |
| volume mute | Mute speakers. |
| volume unmute | Unmute speakers. |
| Pause voice commands<br>Pause voice commands for half an hour | Pause wake-word listening for 30 minutes by default; dictation remains available. Resume automatically or from the tray. |

### Display and Power

| Say | Action |
|---|---|
| Brightness up<br>Brightness up 20% | Increase supported backlight brightness by 5 points, or the spoken amount. |
| brightness down | Decrease supported backlight brightness by 5 points, or the spoken amount. |
| Brightness 70% | Set absolute backlight brightness (0–100%). |
| lock screen | Request the desktop session lock. |
| screen saver<br>screensaver | Activate the desktop screen saver; desktop lock settings still apply. |
| screen off | Turn off X11 displays using DPMS, without suspending. |
| suspend computer | Ask the system to suspend, subject to its normal authorization policy. |
| do not disturb on | Disable desktop notification popups. |
| do not disturb off | Enable desktop notification popups. |

### Media Playback

| Say | Action |
|---|---|
| play | Resume or start playback. |
| pause | Pause playback. |
| play pause | Toggle playback. |
| next track | Skip to the next track. |
| previous track | Return to the previous track. |
| stop playback | Stop media playback. |

### Screenshots

| Say | Action |
|---|---|
| take screenshot | Capture the full screen using the desktop's screenshot tool and normal save behavior. |
| screenshot window | Capture the application window remembered at wake-word activation. |

### Window Management

| Say | Action |
|---|---|
| show desktop | Show the desktop. |
| switch window | Switch to the previous window, relative to the remembered window. |
| maximize window | Maximize the remembered application window. |
| minimize window | Minimize the remembered application window. |
| window left | Fit the remembered window into the left half of its monitor's work area. |
| window right | Fit the remembered window into the right half of its monitor's work area. |
| close window | Request a normal close; applications can prompt about unsaved work. |

### Applications

| Say | Action |
|---|---|
| Open browser<br>Open web browser | Open the preferred web browser configured in your desktop's Preferred Applications settings. |
| Open file manager<br>Open files | Open the preferred file manager configured in your desktop's Preferred Applications settings. |
| Open email<br>Open mail | Open the preferred mail application configured in your desktop's Preferred Applications settings. |
| Open terminal<br>Open terminal emulator | Open the preferred terminal configured in your desktop's Preferred Applications settings. |
| Open calculator | Open the preferred calculator configured in your desktop's Preferred Applications settings. |
| Open text editor<br>Open editor | Open the preferred text editor configured in your desktop's Preferred Applications settings. |
| Open music player<br>Open music | Open the preferred music player configured in your desktop's Preferred Applications settings. |
| Open video player<br>Open video | Open the preferred video player configured in your desktop's Preferred Applications settings. |
| Open image viewer<br>Open photos | Open the preferred image viewer configured in your desktop's Preferred Applications settings. |
| Open pdf reader<br>Open pdf viewer | Open the preferred PDF reader configured in your desktop's Preferred Applications settings. |
| Open word processor<br>Open word processing | Open the preferred word processor configured in your desktop's Preferred Applications settings. |
| Open spreadsheet<br>Open spreadsheets | Open the preferred spreadsheet application configured in your desktop's Preferred Applications settings. |
| Open code editor<br>Open source code editor | Open the preferred source code editor configured in your desktop's Preferred Applications settings. |
| Open Firefox<br>Open Nemo<br>Open Xed | Launch a specific installed application by its display name, executable name or desktop-entry ID (for example Nemo, even when its menu label is Files). Ambiguous or approximate names require confirmation. |

### Clipboard

| Say | Action |
|---|---|
| copy that | Copy the last completed dictation; commands never replace it. |
| paste that | Insert the last completed dictation into the field remembered at wake-word activation, with safe recovery if unavailable. |
| clear clipboard | Clear clipboard contents without deleting the last dictation. |

### Dictation Formatting

| Say | Action |
|---|---|
| dot dot dot | Insert dot dot dot in dictation when Spoken Punctuation is enabled. |
| new paragraph | Insert new paragraph in dictation when Spoken Punctuation is enabled. |
| new line | Insert new line in dictation when Spoken Punctuation is enabled. |
| tab | Insert tab in dictation when Spoken Punctuation is enabled. |
| question mark | Insert question mark in dictation when Spoken Punctuation is enabled. |
| exclamation mark | Insert exclamation mark in dictation when Spoken Punctuation is enabled. |
| exclamation point | Insert exclamation point in dictation when Spoken Punctuation is enabled. |
| full stop | Insert full stop in dictation when Spoken Punctuation is enabled. |
| period | Insert period in dictation when Spoken Punctuation is enabled. |
| comma | Insert comma in dictation when Spoken Punctuation is enabled. |
| semicolon | Insert semicolon in dictation when Spoken Punctuation is enabled. |
| colon | Insert colon in dictation when Spoken Punctuation is enabled. |
| left parenthesis | Insert left parenthesis in dictation when Spoken Punctuation is enabled. |
| open parenthesis | Insert open parenthesis in dictation when Spoken Punctuation is enabled. |
| right parenthesis | Insert right parenthesis in dictation when Spoken Punctuation is enabled. |
| close parenthesis | Insert close parenthesis in dictation when Spoken Punctuation is enabled. |
| left bracket | Insert left bracket in dictation when Spoken Punctuation is enabled. |
| open bracket | Insert open bracket in dictation when Spoken Punctuation is enabled. |
| right bracket | Insert right bracket in dictation when Spoken Punctuation is enabled. |
| close bracket | Insert close bracket in dictation when Spoken Punctuation is enabled. |
| left brace | Insert left brace in dictation when Spoken Punctuation is enabled. |
| open brace | Insert open brace in dictation when Spoken Punctuation is enabled. |
| right brace | Insert right brace in dictation when Spoken Punctuation is enabled. |
| close brace | Insert close brace in dictation when Spoken Punctuation is enabled. |

Formatting converts literal command words too; disable Spoken Punctuation when you want words such as “period” to remain literal. Explicit spoken punctuation is coordinated with existing sentence handling to avoid duplicate punctuation.

## Talk Back setup and sizes

1. Open Preferences → Talk Back.
2. Turn on spoken acknowledgment. If files are missing, guided setup asks for Engine, Model/Voice and Download/Install. Choose Piper and download its shared engine/voice, or choose eSpeak NG and Install eSpeak NG through the desktop installer. Check Again detects it; no restart is needed. The final Enable button activates only after setup is ready.
3. Set Spoken acknowledgment (default Hello) and choose Test Voice. Testing does not save the choices.
4. Enable spoken acknowledgment and Save. Command & Control must also be enabled to use wake acknowledgments.
5. Say your wake phrase alone, wait for the spoken response, then say the command. Combined wake-plus-command speech skips the acknowledgment.

| Engine / voice | Download size | Notes |
|---|---:|---|
| Lessac — English (US), medium | 63.2 MB voice; 97.4 MB on first setup | Natural US English · about 63 MB for the voice, plus 34 MB for the optional engine. |
| Lessac — English (US), high | 113.9 MB voice; 148.1 MB on first setup | Higher-quality US English · about 114 MB for the voice, plus the optional engine. |
| Amy — English (US), low | 63.1 MB voice; 97.3 MB on first setup | Alternative US English voice · about 63 MB, plus the optional engine. |
| English (US) — system | No DictaDesk download | Small, robotic system voice. Uses installed eSpeak NG; no voice-model download. |
| English (UK) — system | No DictaDesk download | Small, robotic system voice. Uses installed eSpeak NG; no voice-model download. |

Piper's shared optional engine is about 34.2 MB downloaded; installed/extracted size is larger. Piper downloads currently target Linux x86-64. eSpeak NG uses the distribution's installed shared library and voice data. Synthesis and playback are local; spoken output is the wake acknowledgment in this experimental feature.

## Platform limitations and test results

- Primary target is Cinnamon/X11. Global shortcuts, original-window operations and automatic field insertion are not implemented through unsupported Wayland focus manipulation. Where insertion is unavailable, text falls back to the clipboard.
- Exact original-field restoration requires usable accessibility objects. Browsers, terminals, Electron applications and custom editors differ. Navigation, content edits, closed fields/windows, password controls and stale objects can require clipboard fallback.
- Brightness depends on supported backlight/desktop interfaces; external monitors may not expose them. DND depends on supported desktop settings, media controls require a compatible MPRIS player, and screenshots use installed desktop tools. Power actions retain normal session authorization boundaries. DictaDesk does not run as root.
- Window targets are captured when the local recognizer identifies the wake phrase; this remains utterance-based recognition rather than a separate streaming keyword engine. Recognition timing and audio quality vary by laptop and speaker.
- Available automated unit tests: **307 passed**. Lint and source compilation passed.
- Isolated GTK/X11 checks passed for the three Preferences tabs, a 1024×600 layout, independent Help pages, closing/Save/Cancel, explicit model setup and microphone retesting, tray lifecycle, repeated recording after failures, clipboard fallback and extended dictation.
- Installed-package checks passed with network disabled for runtime imports, tray-only startup, Preferences/Help/shutdown, optional speech settings/preview cancellation, guided feature setup, installer-cancel/recheck and switch/removal/retry flows, repeated-recording recovery, original-field X11/AT-SPI restoration, preferred applications and explicit Nemo, and private D-Bus/X11 system controls. The newest package was additionally checked for the noise-gate fix and explicit wake feedback.
- Real local recognition checks comprised 25 generated-speech trials, including background RMS 0.04/0.06 and DC offset 0.08, including default/custom wake phrases and the one-utterance command form, with no physical microphone, playback or system action. The Computer/default and custom Computer-phrase positive/negative trials passed. A generated legacy Clanker case still missed recognition during an earlier run, consistent with the reason for changing the default; no claim is made that a configurable phrase always recognizes reliably.
- Real Piper Lessac medium/high and Amy low, plus eSpeak US/UK, produced nonzero PCM and switched/closed successfully. These were synthesis tests with playback replaced by an output fixture.
- **Still needs your laptop testing:** physical microphone/wake performance and actual APT installer/package installation on the laptop (installer launch/completion were mocked in UI tests), audible Talk Back quality/volume/echo, real desktop app focus changes and panel scaling, real screenshot/audio/power hardware behavior. No fresh Mint VM, Wayland acceptance or paid live remote transcription was completed for this build. Mock/private-bus integrations establish requests and state handling, not every hardware action on the target laptop.

## Main implementation areas

- `ui/preferences.py` and `ui/speech_preferences.py`: top-level tabs and optional speech settings.
- `ui/usage.py` and `desktop/tray.py`: separate Help entry points and registry-generated reference. `ui/feature_setup.py` provides the shared three-step enable workflow.
- `core/commands.py`, `core/voice_commands.py`, `core/handsfree.py`, `audio/handsfree.py`: registry, bounded wake matching, completed-utterance command lifecycle and acknowledgment integration.
- `core/application_roles.py`, `desktop/applications.py`, `desktop/commands.py`, `desktop/window_commands.py`: preferred applications, deterministic system integrations and remembered-window actions.
- `core/tts.py`, `audio/speech.py`, `audio/tts_worker.py`: verified optional engines/voices, local synthesis, previews and cancellable playback.
- `core/session.py`, `audio/input.py`, `audio/service.py`, `audio/worker.py`, `core/diagnostics.py`: disposable recording state, bounded recognition, microphone cleanup and private metadata diagnostics.
- `desktop/destination.py`, `desktop/accessibility.py`, `desktop/lock.py`, `ui/application.py`: original-field verification, focus restoration, clipboard fallback, lock state and tray lifecycle.
- State icon resources, focused unit/integration tests and package checks were updated. Obsolete `ui/window.py` and main-window-only tests were removed.

This guide is for the local test build; publication remains on hold pending your installation and testing.


## 11. Final transfer package status

Code commit: `767ee69dcb823311a9f14daac789de5cb50e3b75`. A documentation-only handoff commit follows it.

Latest deb: `/home/erick/dictadesk_1.2.0_amd64.deb` (also copied under the project `dist/`). Copy the deb with the project or rebuild it on the laptop. Installing the older 1.2.0 with the same filename/version will not contain the fix.

Latest deb SHA256: `2e3fae271b93262ff9c52f1d8b26e5315252f24d3dab30feb8979d1187458998`.

Final isolated installed-package checks for this fix passed: startup/tray/Preferences/Help/shutdown; explicit wake feedback; guided enable/install/recheck/cancel; Talk Back; repeated recording/clipboard recovery; extended dictation; registry Help; original X11/AT-SPI destination; and the 25 generated real-ASR noise/offset/wake trials. Package source/resource bytes match the frozen code export. Physical laptop testing is still required.

No publication, push, real desktop installation or restart was performed. The user is copying this folder next. Resume by reading sections 1–3, checking the installed/source identity, and validating the actual laptop microphone against the new gate.
