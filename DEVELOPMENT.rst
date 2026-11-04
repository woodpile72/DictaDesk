Development
===========

Use Python 3.12 on Linux Mint 22.3 / Ubuntu 24.04 with python3-venv,
python3-gi, python3-cairo, python3-gi-cairo, gir1.2-gtk-4.0, gir1.2-adw-1,
libportaudio2 and libsndfile1 installed::

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
    scripts/test-ui .venv/bin/python tests/button_dictation_acceptance.py
    scripts/test-ui .venv/bin/python tests/error_reporting_acceptance.py
    scripts/test-ui .venv/bin/python tests/shortcut_loading_acceptance.py
    scripts/test-ui .venv/bin/python tests/startup_acceptance.py

Desktop checks use private HOME/XDG directories, Xvfb and a private session bus.
Never run them against a user's live desktop. Screenshot capture uses actual GTK
widgets with demonstration state: ``tests/screenshot_capture.py`` through the same
wrapper. No screenshots should expose credentials or personal transcripts.

Optional ``tests/real_speech.py --download-model`` downloads a local test model
and generates speech with libespeak-ng and FFmpeg. ``tests/real_continuous.py``
reuses it, including ``--live`` and ``--window`` variants. Evaluation methodology
and opt-in model comparisons are in ``tests/MODEL-EVALUATION.rst``. These fixtures
never open the real microphone. Remote API tests use fixture responses; they are
not proof of live account permissions, billing or transcription quality.

Packaging
---------

Use even-numbered minor and patch releases; advance the patch by two for fixes.
Publish 1.x versions as regular GitHub releases, marking the newest as Latest.

From the reviewed release tag, use the Python 3.12 environment above::

    git checkout v1.0.10
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
