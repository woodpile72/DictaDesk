# DictaDesk development handoff

Updated 2026-11-08 for the 1.2.0 release preparation. Historical transfer notes and local checks are preserved in ignored `.qa/release-1.2.0/` rather than mixed into the current instructions.

## Local 1.2.2 development

Added local-recognition web search with exact search/search for/search up prefixes. DuckDuckGo is the default provider; Google and Bing are selectable under Control → Web Search. Queries are encoded into HTTPS URLs and opened asynchronously in the desktop default browser, with existing generation/lock guards. Only query text reaches the chosen web service; audio remains with local recognition. Search arguments retain punctuation and are never treated as shell input or another action. Old settings default to DuckDuckGo. This is a local test build; 1.2.0 remains the published release.

## Current state and authorization

- Local version 1.2.2; published version 1.2.0. Cinnamon/X11, amd64, Python 3.12, GTK4 and libadwaita.
- The user authorized publication of 1.2.0, which is complete. Later local builds require a new publication request.
- Future local builds are authorized to reinstall the deb and restart the app. Quit gracefully first; verify the installed source and new process.
- Do not mention or promote Control or Speech in the public release title or notes. Keep public release notes and commit messages generic. The comprehensive local changes/usage guide in `dist/` explains all features.
- Never send announcements to other people unless explicitly requested.

## Current behavior

Preferences opens on **General**, followed by **Dictation**, **Control** and **Speech**. General owns shared microphone/testing, backend/models, credentials, storage, startup and tray appearance. Dictation retains keyboard shortcuts, timing, formatting, local insertion and vocabulary. Dictation and Control link to shared recognition settings. Internal page IDs and saved settings remain compatible.

The wake capture uses the adaptive AC-RMS ambient gate in `audio/handsfree.py`; it must not wait forever for a fixed absolute silence level. Keep generation guards, lock detection, microphone ownership and complete-utterance requirements. The user confirmed physical wake listening works, but a loud television requires louder speech. Do not describe that as noise-robust hardware acceptance.

The wake-detected/checking-command popup was removed at the user's request. The configured beep/spoken acknowledgment, Preferences activity and last-check status remain. Outcome/error notices remain. Screensaver recognizes the exact whole-command ASR variants screenshaver, screen shaver, screensaber and screen saber in either command flow. Arbitrary sentences and application names are never rewritten or executed fuzzily.

## Preserve these constraints

- Tray-only lifecycle; no main/transcript window or blocking transcript recovery queue.
- Standard and extended dictation never execute system commands. Super+Shift+V remains extended dictation.
- Capture the original editable destination at session start; verify it before insertion. Password, stale or unverified fields require clipboard recovery. Empty recognition must not overwrite the clipboard.
- Optional Control and Speech stay off by default. Local recognition is shared with dictation. No microphone mute/unmute commands.
- Strict registry-based commands; no shell execution from recognized speech. Respect desktop lock and normal authorization.
- Downloads require explicit actions. Optional synthesis failure falls back to the beep and must not disable dictation.
- Keep tray geometry stable and asynchronous cleanup/generation protections intact.

## Validation and release workflow

Run unit tests with the locked runtime dependencies, Ruff and compilation. Use `scripts/test-ui` for GTK/X11 checks with private HOME/XDG, display and session bus. Never run integration fixtures directly on the real desktop. Firefox accessibility/paste checks can be sensitive to parallel execution; rerun alone to distinguish timing failures.

The current unit count is **308**. Release evidence is saved under ignored `.qa/release-1.2.0/`. Generated screenshots use synthetic state and contain no user transcripts or credentials. Test environments/models/wheels are local artifacts, not repository content.

Build release packages from a clean exported commit using `scripts/build-deb`; `requirements.lock` pins vendored runtime wheels. Verify packaged and installed source/resource bytes, checksums, installed startup/Preferences/Help/shutdown and successful restart. Keep the full local guide and checksum alongside the latest deb. Publish only the reviewed artifacts tied to the release tag.

## Known limits

Primary target is Cinnamon/X11. Wayland focus/global-shortcut/window manipulation, ARM, fresh-machine dependency installation, full screen-reader acceptance and paid live remote transcription are not established by isolated tests. Hardware brightness, power, screenshots, real output/echo and optional installer behavior vary by desktop. Generated speech and private-bus controls do not establish physical hardware performance.

See `DEVELOPMENT.rst` for dependency setup and test commands, `README.rst` for public usage, and `dist/dictadesk_1.2.0_changes-and-usage.md` for the complete local guide.
