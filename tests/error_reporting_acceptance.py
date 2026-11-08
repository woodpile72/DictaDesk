# SPDX-License-Identifier: GPL-3.0-only
# ruff: noqa: E402
"""Real notification delivery/actions; simulated failures, no microphone or network."""

import os
import time
from pathlib import Path
from unittest.mock import patch

assert os.environ["HOME"].startswith("/tmp/dictadesk-test.")
from dictadesk.ui.application import Application
from dictadesk.ui.gtk import GLib, Adw, Gtk
from notification_host import NotificationHost

Adw.init()
Gtk.Settings.get_default().set_property("gtk-enable-animations", False)
app = Application(isolated=True)
app.notifications_enabled = True
host = NotificationHost()
errors = []
Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)


class Worker:
    loaded = False

    def close(self):
        self.loaded = False

    def request(self, *args, **kwargs):
        raise RuntimeError("Fixture missing model files.")


def checked(fn):
    try:
        fn()
    except Exception as exc:
        import traceback

        traceback.print_exc()
        errors.append(exc)
        app.request_quit()


def until(condition, fn):
    deadline = time.monotonic() + 8

    def poll():
        if time.monotonic() > deadline:
            errors.append(AssertionError("Timed out: " + fn.__name__))
            app.request_quit()
            return False
        if not condition():
            return True
        checked(fn)
        return False

    GLib.timeout_add(30, poll)


def title():
    return host.messages[-1][3]


def shot(name):
    import subprocess
    from gi.repository import GdkX11

    out = Path(__file__).resolve().parents[1] / ".qa"
    deadline = time.monotonic() + 0.1
    while time.monotonic() < deadline:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        time.sleep(0.005)
    xid = GdkX11.X11Surface.get_xid(app.dialog_parent().get_surface())
    subprocess.run(["import", "-window", str(xid), str(out / (name + ".png"))], check=True)


def start():
    app.session.recognizer = Worker()
    app.session.microphone_problem = "Fixture selected microphone was unplugged."
    for _ in range(5):
        app.refresh()
    until(lambda: len(host.messages) == 1, microphone_notice)


def microphone_notice():
    assert title() == "Microphone unavailable"
    assert not hasattr(app, "window")
    host.activate_last()
    until(lambda: app.preferences_dialog is not None, microphone_preferences)


def microphone_preferences():
    p = app.preferences_dialog
    assert p.microphone_status.get_visible()
    assert "unplugged" in p.microphone_status.get_subtitle()
    shot("error-microphone")
    app.session.microphone_problem = ""
    app.shortcut_problem = "Super+V is already used by another application."
    for _ in range(5):
        app.refresh()
    until(lambda: len(host.messages) == 2, shortcut_notice)


def shortcut_notice():
    assert title() == "Shortcut unavailable"
    p = app.preferences_dialog
    assert p.shortcut_status.get_visible()
    assert "Super+V" in p.shortcut_status.get_subtitle()
    shot("error-shortcut")
    app.shortcut_problem = ""
    p.dialog.close()
    app.session.load(local_only=True)
    until(lambda: not app.session.busy and len(host.messages) == 3, load_failure)


def load_failure():
    assert title() == "Model couldn’t load"
    assert "Fixture" not in host.messages[-1][4]
    assert app.session.problem[1] == "loading"
    for _ in range(5):
        app.refresh()
    host.activate_last()
    until(lambda: app.preferences_dialog is not None, model_preferences)


def model_preferences():
    p = app.preferences_dialog
    assert p.attention_group.get_visible()
    assert "missing model files" in p.attention.get_subtitle()
    assert "Repair Model" in p.attention.get_subtitle()
    shot("error-model")
    p.dialog.close()
    app.session.recognizer.loaded = True
    app.session.preferences.preview_pauses = False
    capture.start()
    app.session.record()
    until(lambda: not app.session.busy and len(host.messages) == 4, capture_failure)


def capture_failure():
    capture.stop()
    assert title() == "Dictation stopped"
    assert not any(n[3] == "Recording started" for n in host.messages)
    assert not any(n[3] == "Dictation finished" for n in host.messages)
    c = app.session
    # Exercise a successful worker whose result handler rejects silent audio.
    ticket = c._begin("starting")
    c._dictation_operation = c.operation
    c._recording_event(c.operation, "started")

    def silent(_):
        raise ValueError(
            "No speech was recognized. Your previous transcript is still available."
        )

    c._run(ticket, lambda *_: None, silent)
    until(lambda: not c.busy and len(host.messages) == 7, silent_failure)


def silent_failure():
    assert title() == "No speech detected"
    assert host.messages[-1][4] == "Try again."
    assert [n[3] for n in host.messages[-3:]] == [
        "Recording started",
        "Recording stopped",
        "No speech detected",
    ]
    c = app.session
    save.start()
    c.save_preferences(
        c.preferences, lambda: errors.append(AssertionError("Failed save reported success"))
    )
    until(lambda: not c.busy and len(host.messages) == 8, save_failure)


def save_failure():
    save.stop()
    assert title() == "Settings weren’t saved"
    assert app.session.problem[1:] == ("saving", "Fixture settings permission denied.")
    host.activate_last()
    until(lambda: app.preferences_dialog is not None, finish)


def finish():
    assert "permission denied" in app.preferences_dialog.attention.get_subtitle()
    assert len(host.messages) == 8
    assert not hasattr(app, "window")
    print(
        "PASS error reporting: microphone, shortcut, model, pre-capture failure, silence, failed save, deduplication, real notification actions, tray-only application"
    )
    app.request_quit()


capture = patch(
    "dictadesk.core.session.Capture.run",
    side_effect=RuntimeError("Fixture microphone disconnected before capture."),
)
save = patch(
    "dictadesk.core.settings.SettingsStore.save",
    side_effect=PermissionError("Fixture settings permission denied."),
)
with (
    patch("dictadesk.core.session.check"),
    patch("dictadesk.core.session.microphones", return_value=[]),
):
    GLib.timeout_add(250, lambda: (checked(start), False)[1])
    app.run(["dictadesk-error-test"])
assert not errors, errors
