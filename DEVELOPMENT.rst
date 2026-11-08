Development
===========

Use Python 3.12 on Linux Mint 22.3 / Ubuntu 24.04 with python3-venv,
python3-gi, python3-cairo, python3-gi-cairo, gir1.2-gtk-4.0, gir1.2-adw-1,
libportaudio2, libsndfile1, gir1.2-atspi-2.0, at-spi2-core and pulseaudio-utils installed::

    python3 -m venv --system-site-packages .venv
    .venv/bin/pip install -e .
    .venv/bin/pip install onnx ruff
    .venv/bin/dictadesk

The app is split into core, audio, desktop and ui packages. Keep blocking work
in workers and dispatch GTK updates through GLib. Preserve focus checks,
transcript recovery, private state and cancellation. Use standard native widgets.

Checks
------

Unit tests do not record audio, download models or call a paid API::

    .venv/bin/python -m unittest discover -s tests -v
    .venv/bin/ruff check src tests scripts

Install xvfb, dbus-x11 and imagemagick for isolated desktop checks::

    scripts/test-ui .venv/bin/python tests/ui_acceptance.py
    scripts/test-ui .venv/bin/python tests/preferences_window_acceptance.py
    DICTADESK_TEST_SCREEN=1024x600x24 scripts/test-ui .venv/bin/python tests/preferences_window_acceptance.py
    scripts/test-ui .venv/bin/python tests/x11_acceptance.py
    scripts/test-ui .venv/bin/python tests/tray_acceptance.py
    scripts/test-ui .venv/bin/python tests/delivery_acceptance.py
    scripts/test-ui .venv/bin/python tests/continuous_acceptance.py
    scripts/test-ui .venv/bin/python tests/destination_acceptance.py
    scripts/test-ui .venv/bin/python tests/firefox_destination_acceptance.py
    scripts/test-ui .venv/bin/python tests/error_reporting_acceptance.py
    scripts/test-ui .venv/bin/python tests/shortcut_loading_acceptance.py
    scripts/test-ui .venv/bin/python tests/startup_acceptance.py

Desktop checks use private HOME/XDG directories, Xvfb and a private session bus.
Never run them against a user's live desktop. Screenshot capture uses actual GTK
widgets with demonstration state: ``tests/screenshot_capture.py`` through the same
wrapper. No screenshots should expose credentials or personal transcripts.

Optional ``tests/real_speech.py --download-model`` downloads a local test model
and generates speech with libespeak-ng and FFmpeg. ``tests/real_continuous.py``
reuses it, including the ``--live`` variant. Evaluation methodology
and opt-in model comparisons are in ``tests/MODEL-EVALUATION.rst``. These fixtures
never open the real microphone. Remote API tests use fixture responses; they are
not proof of live account permissions, billing or transcription quality.

Packaging
---------

Use even-numbered minor and patch releases; advance the patch by two for fixes.
Publish 1.x versions as regular GitHub releases, marking the newest as Latest.

From the reviewed release tag, use the Python 3.12 environment above::

    git checkout v1.2.0
    scripts/build-deb

``requirements.lock`` pins private runtime wheels and their SHA-256 hashes.
The build downloads those dependencies into ignored ``.build/`` and packages them
under ``/usr/lib/dictadesk/vendor`` with upstream license files. It performs no
install-time pip operations and includes no model weights. Artifacts and checksums
are written to ignored ``dist/``. Build from a clean exported commit for releases.
The amd64 lock is not an ARM dependency set.

Package models live in ``/var/cache/dictadesk/<uid>``. Source runs instead use
``~/.cache/dictadesk/models``. Configuration and recording workspaces use the user's
XDG directories. Package upgrades preserve models; remove/purge deletes only the
package's dedicated model cache, not user settings or exported transcripts.

Review staged files and history before publication. Exclude credentials, local
paths, models, generated audio, virtual environments, wheels and installer binaries.
Keep GPL-3.0-only and the required third-party notices. New claims about desktop,
language, hardware or accessibility support need corresponding verification.

Release validation
------------------

The 1.0 release passed 156 unit tests and the isolated GTK/X11 checks above,
including high contrast, notification actions, tray recovery and startup behavior.
Package checks use a disposable dpkg root with a private HOME, D-Bus session and
Xvfb display. A mount sandbox supplies the installed package at its normal paths
and read-only host libraries; network access is disabled. ``tests/package_smoke.py``
checks installed imports, window creation, About, Preferences, tray and shutdown.
This does not establish clean-machine dependency installation or Wayland support.

The installer intentionally carries private upstream Python wheels. Lintian reports
embedded libraries, upstream unstripped binaries and wheel-script packaging
conventions; this is a GitHub-distributed release, not a Debian archive package.
The sticky model-cache directory permits separate private per-user subdirectories.
Upstream binary contents and their license notices are preserved.

The 1.0.4 pause update adds a separate sentence-pause setting (4.5 seconds),
keeps short-pause audio context within bounded recognition windows and withholds
provisional sentence endings. Existing saved short-pause settings are preserved.
The update passes 165 unit tests, isolated GTK checks and generated-speech
Parakeet tests in pause, live and in-window modes. Synthetic speech checks
insertion and cleanup; it does not establish accuracy for every voice or language.

The 1.0.8 Preferences update passes 170 unit tests and isolated window checks
at 1920x1080 and 1024x600, including nested dialogs, Escape, Save and Cancel.
Large-text/high-contrast and continuous/button dictation checks also pass.
Preferences has its own resizable window; the main window keeps its size.

The 1.0.10 tray update passes 170 unit tests and isolated GTK, tray and startup
checks. Preferences opens from the tray without mapping the main window,
including at 1024x600 after background startup. Closing or saving Preferences
preserves the main window’s visibility. Tray loss still restores access.

The 1.0.12 timing update passes 173 unit tests, including custom silence-stop
durations, Off, old-settings defaults and saved-value preservation. Isolated GTK
checks cover numeric entry, Save/Cancel, narrow/large-text/high-contrast layouts
and continuous text insertion. Sentence-ending defaults are now four seconds;
explicit saved values remain unchanged.

Local voice commands (unreleased)
----------------------------------------

The optional Voice Commands section is visible only with Local transcription.
It is off by default, requires an installed local model, and keeps that model
resident while enabled. Remote mode suspends it, even if a local preference
was saved as enabled. Ordinary shortcut/button dictation retains priority.

The wake phrase defaults to "Clanker"; saved custom phrases are preserved. Matching uses complete normalized words,
without fuzzy matching. Pause after saying it, wait for the acknowledgment and
brief echo guard, then say "open Firefox", "launch Calibre" or "cancel".
The command capture window is eight seconds. Capture is closed during recognition,
acknowledgment and application launch feedback. Wake buffers contain at most
six seconds of PCM; command buffers at most eight seconds; neither is written
to disk. These are ordinary local recognition models, not speaker identification:
audible speech from other people or media can activate the configured phrase.

Blue means local wake listening; slowly flashing blue means an activated command.
Normal dictation remains red. Loading, recognition and unavailable states retain
the existing indicators. The tray has a local-only hands-free toggle.
Application names resolve through installed desktop entries. Explicitly hidden
and NoDisplay entries are excluded; desktop-specific menu visibility is not a
launch restriction. Exact display names and executable names take priority.
Whole-word prefixes and approximate spellings offer a chooser, even for a single
suggestion; ambiguous exact names also require a choice. Speech is never
passed to a shell. There are no destructive commands or remote command requests.

Extra isolated checks::

    .venv/bin/python -m unittest discover -s tests
    scripts/test-ui .venv/bin/python tests/voice_commands_acceptance.py
    DICTADESK_TEST_SCREEN=1024x600x24 ADW_DEBUG_HIGH_CONTRAST=1 scripts/test-ui .venv/bin/python tests/voice_commands_acceptance.py
    .venv/bin/python tests/profile_voice_commands.py --model-cache /path/to/existing/model-cache

The profiler uses an existing model, offline model loading, synthetic English
speech, and real-time synthetic silence. It never opens a microphone or plays
sound. On the development machine, Parakeet Int8 on CPU used 0.27% of one core
and about 1.16 GiB combined parent/worker RSS over 30 seconds of silence, with
zero recognition calls during silence. Model loading took 2.36 seconds and
short generated utterances took 59–83 ms to recognize. RSS sums can include
shared pages; these figures exclude real microphone/driver and GTK overhead.

Synthetic "Hello computer" followed by "open Firefox" passed the complete
coordinator path. Synthetic "Hey Mint" became "Amen", and "Calibre" became
"caliber": exact matching correctly rejected those results. Real voices,
room noise, speaker echo, the audible acknowledgment, and Cinnamon's actual
panel rendering still need human acceptance. Choose a clearer wake phrase if
your model does not recognize it exactly. These are development measurements, not a broad accuracy claim.

Failed commands show the recognized post-activation words and normalized application
query in a transient desktop notification.
Diagnostics are bounded plain text and are not written to application history or
logs. Wake-listening transcripts are never included. The local diagnostics update
passes 205 unit tests and isolated GTK notification/Details checks.

Application suggestions show four numbered entries per page, plus 5 / More when
another page exists. The same local worker recognizes one through four, five,
more/other/next page, and cancel. Each page has a 20-second listening window with
at most eight seconds of buffered speech. Paging aborts capture for the old page;
late audio, recognition and UI callbacks cannot select from a different page.
Dictation, disabling, Remote mode and Escape invalidate the entire chooser.
Generated speech with real Parakeet recognized one through four and more. Generated
five became "Uh" and was rejected; real voice acceptance is still required.

Voice Commands is marked Experimental by a red banner with white text above its
settings. Both banner and settings are hidden in Remote mode.

Screen-lock handling observes the native Cinnamon/GNOME screensaver D-Bus service
(or the freedesktop service on other desktops). It only reads status and subscribes
to changes; it never locks or unlocks the desktop. An initial Cinnamon read may
activate its on-demand service. Its normal idle exit preserves a confirmed inactive
state; losing a service while locked keeps commands paused. Unknown lock status
also pauses unattended commands. A confirmed lock cancels dictation and microphone
samples, clears pending starts, stops command capture and invalidates chooser
callbacks. Unlock may resume enabled wake listening, never the cancelled dictation.

The 205 unit tests cover status races, missing services, capture release and stale
results. ``scripts/test-ui .venv/bin/python tests/lock_acceptance.py`` uses a fake
Cinnamon service on a private bus to verify lock/unlock and application guards.
Continuous dictation, delivery and voice-command GUI checks pass. The live Cinnamon
status was read successfully; the user's desktop was not locked by these tests.

The application chooser uses a dim desktop overlay with a large centered card on
the primary monitor (X11 RandR primary output, with an origin/first-output fallback).
Other displays receive dim covers. Four choices and More fit together at ordinary
text sizes; a scrolling fallback keeps oversized text accessible. Desktop IDs are
only shown for duplicate display names, and remain available as tooltips.
Escape, Cancel, timeout, dictation and screen locking dismiss the covers. This is
application attention UI, not a security lock: operating-system shortcuts remain
available. No screensaver activation or global keyboard grab is used.

Tray-only destinations (1.2.0 in development)
-------------------------------------------

Application startup holds the GTK application without creating any window.
Preferences and Last Transcript create standalone windows only on explicit
activation; closing them does not release the application hold. Missing or lost
tray hosts never map an unsolicited fallback window. ``--background`` remains a
compatible no-op for existing autostart entries.

``desktop/destination.py`` captures the X client/input-focus IDs and PID through
python-xlib. It binds a live AT-SPI object to its accessible ancestry (including
browser documents), the active frame, a content digest and caret/selection.
AT-SPI focus events and bounded initial discovery avoid shell utilities and
continuous accessibility-tree polling. Routine shortcut polling stays on the
existing X connection. Blocking audio/model work stays in existing workers.

Each recording worker closes over its own destination. Streaming stays in that
field; once focus changes, subsequent phrases accumulate until completion.
Delivery validates the original object/window, requests EWMH activation and
AT-SPI field focus, restores its saved selection, and reuses the clipboard/XTest
paste path. An editable field must reflect the expected paste before a phrase is
acknowledged. The next phrase advances that session's caret and digest. Returning
to the previous app is best-effort and does not invalidate successful insertion.
Generation/cancellation checks protect delayed focus and clipboard callbacks.

Defunct or removed fields, changed documents/contents, password fields, missing
accessibility and unverifiable focus use recovery. A deliberate Paste Last
Transcript shortcut authorizes the currently focused X window; automatic delivery
never falls back to an unrelated currently focused window. Unsupported terminals,
custom widgets and Electron editors require manual recovery if they do not expose
an editable AT-SPI field. Wayland tray recordings retain clipboard recovery;
global shortcuts and automatic focus restoration remain X11-only.

Private-desktop checks exercise real GTK3 fields and a fresh, offline Firefox
profile, including original selections, another field in the same window,
another window/application, page reload, removed controls, closed windows,
no editable focus, repeated sessions and clipboard recovery. These checks never
open the user's microphone or change the user's desktop focus. Real microphone
acceptance, unusual application accessibility trees and a live Wayland session
still require human testing. The package is built from an exported release commit; see the release checks below.

Current validation also includes ``tests/usage_acceptance.py`` and
``tests/system_commands_acceptance.py``. The help window derives its command rows,
examples, default percentages and formatting reference from the central registry.
System integration checks use private D-Bus brightness/MPRIS services, memory-only
notification settings and a private EWMH host. They never suspend, blank, lock,
mute or close anything on the user's desktop. Window half-screen placement uses
EWMH geometry requests; it does not emulate a window manager's persistent snap state.
Brightness currently uses Cinnamon's supported backlight service. External-monitor
DDC control and Wayland window manipulation are not implemented. Pause/resume is
session-local: thirty minutes by default, with explicit early resume in the tray.

The 1.2.0 checks cover tray-only startup, original field/selection restoration,
page invalidation, uninserted-text recovery, registry/help consistency and timed
listening pause/resume. Microphone mute/unmute commands are intentionally omitted;
the timed pause affects only wake listening and leaves dictation available.
The synthetic English voice pronounced the default phrase as "Clanker" but
Parakeet recognized "Langer"; exact matching correctly rejected it. Wake accuracy
with the user's real microphone and voice remains a human acceptance check.

Startup verifies the user session's accessibility-bus address before GTK starts.
A stale X11 AT_SPI_BUS property cannot override that verified address. If the
advertised socket is missing or cannot be connected to within the startup bound,
GTK starts without its broken accessibility bridge, field tracking stays disabled,
and Preferences/notifications explain clipboard recovery. This does not change
system accessibility settings or restart other applications' services.
