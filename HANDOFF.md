# DictaDesk 1.2.4 release preparation — 2026-11-17

The user explicitly authorized combining all current changes, building, installing locally, and publishing 1.2.4. This section supersedes older release-state claims below. The source passes 343 unit tests, Ruff, and all 24 isolated desktop acceptance scripts. The test wrapper now detaches private portal FUSE mounts during cleanup and preserves the actual test exit status. Build/install/publication are pending; verify their final state from the release receipt and GitHub release. The release includes the existing matching/confirmation/feedback changes and the fix that reuses microphone capture across discarded wake clips. No machine-wide PipeWire configuration is shipped. Preserve the standing public-notes restriction below.

## Historical handoff for 1.2.2

# DictaDesk — development handoff

Updated **2026-11-09**, after publishing **1.2.2** and reinstalling/restarting that exact release locally. Read this before changing anything. This document summarizes the entire development session and supersedes older “unpublished 1.2.0” / “1.2.2 publication pending” notes.

## 1. Start here: current state

- Project: **`~/Projects/Open/DictaDesk/`**, currently `/home/erick/Projects/Open/DictaDesk`. Capitalization matters on Linux.
- Repository: <https://github.com/woodpile72/DictaDesk>.
- Latest published release: **v1.2.2**, a regular release marked **Latest**: <https://github.com/woodpile72/DictaDesk/releases/tag/v1.2.2>.
- Release/tag source commit: **`39d5a4dac2ab2092366aa7e0ad02954c290c77f2`** (`Prepare 1.2.2 documentation and release metadata`). All implementation in this session is included in that tag and pushed to `origin/main`.
- Exact release deb SHA256: **`666c16568797820103265c544706b2c8a66acff01dd6655e20ebd6da8759e143`**.
- Release assets: `dictadesk_1.2.2_amd64.deb`, `dictadesk_1.2.2_amd64.deb.sha256`, `SHA256SUMS`. GitHub's uploaded deb digest was checked against the local package.
- That exact deb was reinstalled locally and DictaDesk was restarted. Installed source/resource bytes match the release source. The new process and application D-Bus registration were verified.
- Current app version is **1.2.2**. Earlier local test debs also said 1.2.2, so compare checksums/source bytes rather than trusting the version alone.
- Latest package/checksum/full local usage guide are in `dist/` and copied beside one another in the user's home directory. Guide: **`dictadesk_1.2.2_changes-and-usage.md`**; it covers all changes since 1.0.12, including the experimental features.
- **324 unit tests passed**, Ruff and compilation passed, and the release's isolated GTK/X11 and installed-package checks passed. Details below.
- This updated HANDOFF is a documentation-only change after the release. The released app/package remains tied to `39d5a4d`; do not rebuild or replace an immutable published asset merely to update this document.

Both 1.2.0 and 1.2.2 were published in this session. 1.2.0 was published on November 8; 1.2.2 on November 9. Original instructions saying the last public release was 1.0.12 are now historical.

## 2. Standing user instructions

1. **When building, reinstall the resulting deb locally and restart the app.** The user explicitly authorized this as the ongoing workflow. Quit gracefully first; install normally with APT; launch as the normal user; verify the running code. Do not repeatedly ask permission for these steps.
2. **Public release titles/notes must not mention or promote Control or Speech.** Keep release notes and commit messages generic. Those features are simply present in the app. The comprehensive local usage guide and this handoff should explain them fully.
3. Publication of 1.2.0 and 1.2.2 was explicitly authorized and is complete. A future implementation request does not, by itself, authorize another public release.
4. Keep notifications sparse. The wake/acknowledgment beep is enough for ordinary activation, and routine command successes should be quiet. Preserve actionable failures and meaningful timer/recovery feedback.
5. Timer setup should say **“A timer has been set for X time.” only if TTS is enabled in Preferences**. Respect the selected local voice. Do not enable TTS or download voices automatically.
6. **Do not add microphone mute/unmute commands.** A voice-listening pause is separate from system microphone muting.
7. Do not send announcements or messages to other people unless explicitly asked. The user arranged for a Linux Mint forum tester to try the app; no feedback from that tester has been supplied in this chat.
8. Preserve existing working behavior; do not redesign or start over. The user prefers concrete implementation, testing, installation and restart over repeated proposals/confirmation questions.

## 3. Session history and accepted changes

### Initial investigation and 1.2.0

We began by reading the old handoff. The source already contained the adaptive wake gate fix (`767ee69`), but the running installed wake-capture file differed from source. The transferred noise-fix deb matched its recorded checksum. The user installed/tested the fix and reported that wake listening worked, although the Packer game playing in the background required speaking loudly.

We then implemented:

- Removed the **“Wake phrase heard / Checking your command”** desktop popup. Kept beep/optional spoken acknowledgment and Preferences activity/last-check feedback.
- Reorganized Preferences into **General · Dictation · Control · Speech**.
- Renamed “Command & Control” to **Control** and “Talk Back” to **Speech** throughout Preferences, guided setup, Help and applicable labels. Internal page IDs (`commands`, `tts`) and saved settings remain compatible.
- General opens first and contains shared microphone/testing, transcription backend/model controls, credentials, storage, startup and tray appearance. Dictation keeps shortcuts, timing, formatting, local insertion/preview and vocabulary. Dictation and Control link to shared recognition settings in General. The missing-model prompt now belongs to General.
- Added exact, whole-command screensaver ASR variants: **screenshaver**, **screen shaver**, **screensaber**, **screen saber**. This addresses the user's observed mishearings without general fuzzy execution or rewriting application names/sentences.

1.2.0 was then checked, committed, built, installed, restarted, pushed and published as Latest. That release used `86a4c2e`; the current 1.2.2 supersedes it.

### Local work after 1.2.0, now included in 1.2.2

- Added local-recognition **web search** with DuckDuckGo as default and an engine selector.
- Removed routine successful command popups, retaining errors and useful requested feedback.
- Fixed premature search capture and ASR sentence punctuation in query text.
- Added a durable local **countdown timer**, tray/Preferences controls, expiry notification and short audible alarm.
- Simplified the tray menu: no idle timer row and no three Help entries; conditional Cancel timer only.
- Added optional TTS timer-set confirmation and audio/cancellation safeguards.
- Updated README download links, Help location, About screenshot, developer instructions and release metadata for 1.2.2.

### Implementation commits

| Commit | Purpose |
|---|---|
| `767ee69` | Adaptive ambient/AC wake gate and activation feedback, inherited at session start. |
| `86a4c2e` | Shared Preferences, naming and recognition refinements; 1.2.0 release source. |
| `19e3ad2` | Configurable browser search with bounded encoded queries. |
| `d46cc25` | Quiet routine completion; retain errors. |
| `c12a83c` | Natural search pauses, bounded follow-up and punctuation cleanup. |
| `129aa43` | Local countdown, durable state and cancellable alarm output. |
| `f72e418` | Simplified tray and optional spoken timer confirmation. |
| `39d5a4d` | Final 1.2.2 documentation and release metadata; published source. |

## 4. Final web-search behavior

With Control enabled, use either inline wake + command, or wake → acknowledgment → separate command:

- **Computer search Linux Mint**
- **Computer search for Linux Mint**
- **Computer search up Linux Mint**

Substitute the configured wake phrase. The user specifically requested all three prefixes, including “search up.”

- **DuckDuckGo** is the default, chosen explicitly by the user. **Google** and **Bing** are alternatives under **Preferences → Control → Web Search → Search Engine**; Save applies the choice.
- This preference belongs to DictaDesk; it does not alter the browser's own default search engine. Search URLs open through the desktop's default browser using Gio, asynchronously, without shell interpretation.
- Wake/command recognition remains local. Only the search text is sent to the selected provider by the browser. No general remote/AI command interpreter was added.
- Old settings without `search_engine` default to DuckDuckGo.
- Query text is limited to 1000 printable characters. URL encoding keeps punctuation/query text as data. A phrase such as “cats and lock screen” remains one search, not another executed command.
- Trailing ASR sentence punctuation (`. , ; : ! ? …`) is removed from query endings. Meaningful internal punctuation, `.NET`, `C++`, `C#`, decimal numbers and addresses are retained.
- Punctuation between command words, such as **“Search. For Linux Mint.”**, is tolerated.
- **Wake captures end after 1.0 second of quiet; command/query captures after 1.2 seconds.** The low-level `utterance()` default stays 0.5 seconds for other callers.
- If recognition returns only **search**, **search for** or **search up** (including ASR punctuation), it is not rejected immediately. It updates local status, beeps if acknowledgment sound is enabled, and collects **one follow-up query for up to 10 seconds**. Say the query after that beep.
- The follow-up is treated only as query text. Standalone cancel/Escape cancels; no speech returns quietly to listening. Stale, disabled, locked or superseded results cannot open a browser.
- Success is quiet; browser/recognition failures remain visible.

The user confirmed browser searching worked, and responded positively after the pause/punctuation fix.

## 5. Final timer behavior

### Commands

- **Computer set a timer for 24 minutes**
- **Computer set a timer for twenty-four minutes**
- **Computer set a timer for one hour and thirty minutes**
- **Computer how much time is left?**
- **Computer timer status**
- **Computer cancel the timer**
- **Computer cancel timer** — explicitly requested and confirmed supported.
- Also supports **stop timer**, **stop the timer**, **stop alarm**, **stop the alarm**.

The central registry contains the complete aliases. Numeric and common spoken durations, seconds/minutes/hours, combined units and “half an hour” work. Unsupported/negative/decimal/overlong/extra-command input is rejected rather than guessed.

### Countdown and persistence

- Built into DictaDesk; no separate timer app, model, cloud service or new runtime dependency.
- **One timer at a time, from 1 second to 24 hours.** An active timer is not silently replaced; cancel it first. A completed timer can be replaced.
- Timer operation is separate from saving Preferences. Turning off voice listening does not cancel an existing timer.
- Tray tooltip shows time left; **Control Preferences** has countdown status and a cancel button. A spoken status request gives a system notification with the remaining time. It is not currently a spoken time-left response.
- Atomic, private state file: **`~/.local/state/dictadesk/timer.json`**, honoring `XDG_STATE_HOME`. It stores numeric timing/boot metadata, not voice text.
- Uses Linux **CLOCK_BOOTTIME** so suspend counts and wall-clock changes do not disrupt an in-process countdown. Saved boot identity/deadline preserve this across same-boot app restarts; the wall deadline is used after a reboot.
- DictaDesk must be running to alert. It does not wake a sleeping computer. If active state became overdue while closed, it alerts when reopened; after sleep, it alerts on resume.
- Completed state is persisted to avoid repeating an already-fired alarm on every startup. Cancel clears saved state. Corrupt state is reported and can be reset with the cancel control in Preferences.

### Alarm and spoken confirmation

- Expiry posts **one “Timer finished” notification**, with Dismiss, and plays a short synthesized local alarm of roughly six seconds.
- The alarm is finite, not an Alexa-style indefinitely ringing alarm. Hands-free is suspended during the sound; immediate dismissal is available through the notification or tray. Voice cancellation is available when listening resumes.
- Alarm playback waits for active dictation/voice preview and background capture to stop. A new recording stops and joins output first; shutdown waits for native output cleanup. Playback failure reports the problem without discarding dictation functionality.
- Timer creation speaks **“A timer has been set for 24 minutes.”** (or the formatted duration) **only if TTS is enabled in Preferences and the selected local voice is ready**.
- Uses the existing local speech worker/output path; no auto-install/download/enable and no new TTS setting. It does not overwrite the saved wake acknowledgment phrase.
- Manual dictation interrupts confirmation. If the timer expires during its own spoken confirmation, that speech is canceled so the alarm takes priority.
- Otherwise setting/canceling stays quiet. No routine successful-command popup was reintroduced.

The user confirmed the basic timer worked. Final spoken-confirmation behavior was verified with worker/output fixtures; the user has not separately reported a physical-speaker acceptance result for that last addition.

## 6. Tray, Preferences and notifications: preserve these decisions

- **No “No timer running” row**, no permanent timer-status menu item, and no disabled idle Cancel timer item.
- Add **“Cancel timer”** only while the timer is active or its alarm is pending/playing; remove it when no longer needed. Dynamic menu changes emit layout updates and stale property requests fail safely.
- Removed the tray's **Dictation Help**, **Control Help**, and **Speech Help** entries. All three remain accessible from their Preferences pages. Do not put them back in the tray.
- The ordinary recording Cancel item is separate from timer cancellation; it was not removed.
- Keep tray icon geometry constant. No pulsing, flashing or state-dependent size changes.
- Routine successful search, volume/brightness, media/window, screenshot and clipboard copy/clear commands complete quietly. Adapters use an empty completion message for quiet success; do not infer success/failure by matching notification strings.
- Keep errors, unsupported/unknown-command feedback, chooser prompts, listening-pause information, requested timer status, timer expiry and dictation clipboard-recovery notices.
- Wake/command-evaluation popup remains removed. Keep beep/optional spoken acknowledgment and Preferences listening/activity/last-check feedback.

## 7. Existing behavior and safety constraints

- **Tray-only app**; no main dictation/transcript window or pending-text recovery queue. Preferences and Help are independent windows. Closing them returns to the tray.
- Default shortcuts: **Super+V** standard dictation; **Super+Shift+V** extended dictation; **Super+Shift+X** cancel recording; **Super+Shift+P** explicitly paste last completed dictation. All configurable. **Escape** finishes dictation/cancels an active voice command.
- Super+Shift+V is never a system-command mode. Commands spoken during ordinary/extended dictation never execute actions.
- Standard dictation retains silence stopping (20 seconds default; 0 is Off), sentence-ending pause (4 seconds default), update pauses and maximum duration. Extended dictation uses its session limit.
- Capture the original X11 window/editable field at recording start. Verify field identity, caret/selection and contents before insertion; handle focus changes conservatively. Password, stale, navigated-away, unsupported or missing fields require clipboard fallback.
- Valid failed-insertion text goes to the clipboard and another recording is immediately possible. Empty/invalid recognition must not overwrite the clipboard. Last completed dictation stays in memory for explicit copy/paste; commands do not replace it.
- Control and Speech remain optional, experimental and off by default. Dictation has microphone priority. Share the existing local recognition model; do not add a competing wake/command model system.
- Adaptive AC-RMS ambient gate in `audio/handsfree.py` fixes the original absolute-silence stall. Preserve pre-roll, complete-utterance requirements, cancellation generations, lock detection, bounded worker calls and microphone cleanup.
- Do not loosen grammar into general fuzzy execution, arbitrary shell commands or a remote AI action interpreter.
- Voice-listening pause is separate from system microphone muting. No mic mute/unmute commands.
- Optional voices/engines download only on explicit actions. Existing Piper/eSpeak guided setup and installer/recheck flows remain. Optional speech failures must not disable dictation.
- Desktop authorization/lock boundaries remain intact. Run DictaDesk as the normal user, never root. Root is only for ordinary package installation or the isolated mount test wrapper.

## 8. What was tested, and what remains uncertain

### Release evidence

- **324 unit tests passed**; Ruff passed; source compilation passed.
- All **22 isolated GTK/X11 acceptance scenarios** passed, covering timer/TTS, tray, Help/Preferences, setup, repeated recording/recovery, extended dictation, destination restoration, Firefox fields/navigation, lock handling and desktop integrations.
- One parallel run finished its UX assertions successfully but the wrapper failed removing a temporary FUSE/portal directory. The isolated retry exited successfully. This was a test-wrapper cleanup race, not an ignored product assertion.
- Small-display layout was checked at **1024×600** during the session. Screenshots use synthetic state, not personal transcripts/credentials; About screenshot now shows 1.2.2.
- Final release export was compared with every tracked file at the release commit. Packaged source/resources and bundled README were compared with source. Installed source/resources were compared after reinstall.
- Exact final deb passed **installed-path smoke checks with networking disabled**: imports, tray-only startup, Preferences, Help, About and clean shutdown. Timer/TTS UI/routing checks also ran against the extracted package offline with mocked output/notifications.
- Uploaded GitHub checksum matches local release package; release is non-draft, non-prerelease and Latest. Local restart and D-Bus actions were verified.
- Logs/receipt are under ignored **`.qa/release-1.2.2/`**, including `RELEASED.txt`, `unit.log`, per-acceptance logs, `package-smoke.log`, `package-timer-ui.log`, build/install/launch logs. Additional feature evidence is under `.qa/search-1.2.2/`, `.qa/timer-1.2.2/`, `.qa/tray-confirm-1.2.2/`.

### User feedback / remaining practical checks

- User confirmed physical wake listening works, but must speak loudly over a loud TV/game. This remains a known environmental limitation; lowering the old absolute silence threshold is not a general fix. Do not claim noise-robust microphone acceptance.
- User confirmed searching and the basic timer work, and liked the pause/punctuation fix.
- Screensaver ASR aliases are covered by tests; no separate final physical acceptance statement was given for every variant.
- Optional TTS timer confirmation and alarm cancellation/device failure paths have fixture coverage; physical voice quality/volume, speaker echo and every device combination are not established by those tests.
- No fresh Mint VM/dependency installation, Wayland/ARM acceptance, full screen-reader acceptance or paid live remote-transcription request was completed. Brightness/power/screenshots and original-field behavior vary with actual hardware/apps. Private-bus tests establish requests/state handling, not all physical actions.
- No new feature is currently requested beyond this handoff. Do not automatically add multiple/named timers, an always-running timer daemon, cloud command interpretation, system mic controls or new success notifications.

## 9. Important code map

Paths below are relative to `src/dictadesk/`:

| Area | Files |
|---|---|
| Registry, parameters, descriptions, examples and Help source | `core/commands.py` |
| Exact wake/parser/application matching | `core/voice_commands.py` |
| Wake/command lifecycle, follow-up search, generations | `core/handsfree.py` |
| Adaptive gate, in-memory capture, pre-roll, ending pauses | `audio/handsfree.py` |
| Search providers, query cleanup, URL encoding | `core/search.py` |
| Default-browser asynchronous launch | `desktop/search.py` |
| Countdown grammar, state, clock/restart handling | `core/countdown.py` |
| Cancellable synthesized timer alarm | `audio/alarm.py` |
| Command routing, timer polling/notifications/TTS, lifecycle | `ui/application.py` |
| Tray menu and live layout updates | `desktop/tray.py` |
| General/Dictation/Control Preferences and timer status | `ui/preferences.py` |
| Speech settings and preview integration | `ui/speech_preferences.py` |
| Shared speech synthesis/playback, timer confirmation flag | `audio/speech.py`, `audio/tts_worker.py`, `core/tts.py` |
| Registry-based Help pages | `ui/usage.py` |
| Settings/default engine migration/validation | `core/settings.py` |
| Dictation, safe original-field delivery, lock handling | `core/session.py`, `desktop/destination.py`, `desktop/accessibility.py`, `desktop/lock.py` |

New/relevant tests: `tests/test_search.py`, `tests/test_countdown.py`, `tests/test_tts.py`, `tests/test_voice_commands.py`, `tests/test_desktop_commands.py`, `tests/timer_acceptance.py`, `tests/tray_acceptance.py`, `tests/voice_commands_acceptance.py`, `tests/talkback_acceptance.py` and `tests/package_smoke.py`.

## 10. Resume on the other PC

1. Inspect `git status --short` before pulling/copying; preserve local edits. Read this handoff and check `git log -8 --oneline`.
2. Unlike the start of this session, **public main now contains the implementation**, through release commit `39d5a4d`. Fetch/pull the current repository or copy this tree as appropriate. Transfer this updated handoff too: it is newer than the tagged release's handoff.
3. Keep the exact capitalized project path. Avoid copying nonportable `.venv` or large `.build`/`.qa` caches just to transfer source. The current machine rebuilt environments/caches after the old transfer cleanup, so older claims that the whole directory is only 60 MiB no longer describe the current working tree.
4. Git does not include ignored `dist/`, `.qa/`, `.build/`, `.venv`. Get the release deb/checksums from GitHub or copy them explicitly. Copy the full local usage guide if the other Codex needs every feature's usage tables.
5. A source checkout does not update `/usr/lib/dictadesk` or a running process. Identify whether the PC is running installed code or source, and avoid two instances during microphone tests.
6. Settings, models, credentials and timer state belong to each computer/user. Do not blindly sync another machine's live timer state, keyring or microphone preferences as part of a code transfer.

### Fresh development environment

Target: Linux Mint Cinnamon/X11, amd64, Python 3.12, GTK4 4.14+, libadwaita 1.5+. See `DEVELOPMENT.rst` for distro GI/Cairo, audio, accessibility and desktop dependencies.

```bash
cd ~/Projects/Open/DictaDesk
python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install --require-hashes -r requirements.lock
.venv/bin/python -m pip install --no-deps -e .
.venv/bin/python -m pip install ruff
.venv/bin/python -m unittest discover -s tests
.venv/bin/ruff check src tests scripts
.venv/bin/python -m compileall -q src
```

This machine's `.venv` was created fresh and used for pip/build/Ruff; source unit runs reused the installed locked private vendor dependencies with:

```bash
PYTHONPATH=src:/usr/lib/dictadesk/vendor python3 -m unittest discover -s tests
```

That shortcut is useful only when the installed vendor set matches the reviewed lock. It is not a substitute for recreating a proper environment on another PC.

### Isolated UI checks

Always use `scripts/test-ui`, which creates private HOME/XDG directories, Xvfb and a private session bus. **Never run integration fixtures directly on the real desktop.** Install normal test dependencies (Xvfb, xauth, D-Bus, ImageMagick, etc.) as documented.

```bash
scripts/test-ui .venv/bin/python tests/timer_acceptance.py
scripts/test-ui .venv/bin/python tests/tray_acceptance.py
scripts/test-ui .venv/bin/python tests/voice_commands_acceptance.py
scripts/test-ui .venv/bin/python tests/talkback_acceptance.py
scripts/test-ui .venv/bin/python tests/usage_acceptance.py
DICTADESK_TEST_SCREEN=1024x600x24 scripts/test-ui .venv/bin/python tests/preferences_window_acceptance.py
```

Xvfb was initially missing here; an Ubuntu xvfb deb was downloaded/extracted into this chat's `work/ui-tools/` directory and added to PATH for tests, without installing it system-wide. That chat-local binary path will not exist on the other PC. In isolated mount tests, force Mesa/software rendering if the host's NVIDIA EGL stack makes Xvfb crash; this was an environment fix, not an application change.

Portal/PipeWire/DRI warnings are common on the private test bus. Judge assertions and exit codes. If assertions passed but temporary portal/FUSE cleanup failed, rerun in isolation and record the outcome rather than masking the error. Firefox accessibility checks may also need an isolated rerun to distinguish timing issues.

### Build/install/restart workflow

Build from a clean exported commit, not a mixed working directory:

```bash
DICTADESK_PYTHON=/absolute/project/.venv/bin/python /absolute/export/scripts/build-deb
```

The builder uses hash-locked wheels in ignored `.build/wheels` and emits the deb/checksum/SHA256SUMS in `dist/`. No downloaded ASR or TTS weights are bundled. Do not silently regenerate the amd64 dependency lock or add heavyweight baseline dependencies.

For local installation, quit via the tray (or activate the existing `quit` action), install through normal APT, then relaunch as the user. Preserve downloads/preferences. Verify installed/source bytes and that the old process actually exited. Normal app ID is `io.github.woodpile72.DictaDesk`; installed launcher `/usr/bin/dictadesk`; installed package code `/usr/lib/dictadesk/dictadesk`; vendored runtime `/usr/lib/dictadesk/vendor`.

Release packages were checked with root-created bwrap mount isolation, but the actual app/tests ran as the normal account with private HOME/bus/Xvfb and networking disabled. The extracted package was bound at installed paths, and `/var/cache/dictadesk` was a private root-owned sticky directory. Never bind the user's real cache/session bus into those fixtures. Package smoke requires `DICTADESK_PACKAGE_TEST=1`.

**Published 1.2.2 is immutable.** For subsequent app changes, use a new appropriate version rather than replacing the release asset. `DEVELOPMENT.rst` uses even patch increments, so the next ordinary 1.2.x fix release would be 1.2.4. Build/install authorization persists; publication needs an explicit request.

## 11. Local data and reference material

- Preferences: `~/.config/dictadesk/preferences.json` (XDG config override honored).
- API credentials: desktop keyring. Do not dump/export them into logs or handoffs.
- Diagnostics: `~/.local/state/dictadesk/diagnostics.log`, rotating private metadata, no raw audio/transcripts/API keys.
- Timer: `~/.local/state/dictadesk/timer.json` (XDG state override honored).
- Packaged models/voices: `/var/cache/dictadesk/<uid>/`; source-run model cache is under `~/.cache/dictadesk/models/`. Optional speech files are separate from recognition models.
- `README.rst`: public usage and current release links.
- `DEVELOPMENT.rst`: distro setup, testing, packaging and platform limits.
- `dist/dictadesk_1.2.2_changes-and-usage.md`: comprehensive local guide, including all experimental features and command tables.
- `.qa/release-1.2.2/RELEASED.txt`: release receipt/checksum/testing summary, if this ignored directory was copied.
- Older `.qa/release-1.2.0/` holds historical transfer notes. They explain earlier failures but must not override current release state or later user instructions.
