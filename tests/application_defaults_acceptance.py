# SPDX-License-Identifier: GPL-3.0-only
# ruff: noqa: E402
"""Real GIO default associations and Cinnamon settings on a private desktop.

Fixtures touch private marker files; no browser, file manager or terminal opens.
"""

import os
from pathlib import Path
import time
from unittest.mock import patch

assert os.environ["HOME"].startswith("/tmp/dictadesk-test.")
os.environ["GSETTINGS_BACKEND"] = "memory"
os.environ["XDG_CURRENT_DESKTOP"] = "X-Cinnamon"
from dictadesk.core.commands import recognize
from dictadesk.ui.application import Application
from dictadesk.ui.gtk import Gio, GLib

app = Application(isolated=True)
app.model_prompts_shown.add(("parakeet", "int8"))
root = Path(os.environ["HOME"])
applications = Path(os.environ["XDG_DATA_HOME"]) / "applications"
applications.mkdir(parents=True)
fixtures = {}
for ident, name, mime in (
    ("browser-one", "First Browser", "x-scheme-handler/http;"),
    ("browser-two", "Second Browser", "x-scheme-handler/http;"),
    ("nemo", "Files", "inode/directory;"),
    ("other-files", "Other File Manager", "inode/directory;"),
    ("terminal-one", "Terminal One", ""),
    ("terminal-two", "Terminal Two", ""),
    ("calculator-one", "Calculator One", ""),
    ("editor-one", "Editor One", "text/plain;"),
    ("mail-one", "Mail One", "x-scheme-handler/mailto;"),
):
    path = applications / (ident + ".desktop")
    path.write_text(
        f"[Desktop Entry]\nType=Application\nName={name}\nExec=/usr/bin/touch {root / ident} %U\nMimeType={mime}\n"
    )
    fixtures[ident] = Gio.DesktopAppInfo.new_from_filename(str(path))

errors, feedback = [], []
terminal = Gio.Settings.new("org.cinnamon.desktop.default-applications.terminal")
calculator = Gio.Settings.new("org.cinnamon.desktop.default-applications.calculator")


def checked(fn):
    try:
        fn()
    except Exception as error:
        import traceback

        traceback.print_exc()
        errors.append(error)
        app.request_quit()


def later(fn):
    GLib.timeout_add(200, lambda: (checked(fn), False)[1])


def launch(phrase, marker, next_step):
    app.handsfree.stop.clear()
    kind, query = recognize(phrase)
    assert kind == "open"
    app.launch_command(query, app.handsfree.generation, heard=phrase)
    deadline = time.monotonic() + 3

    def poll():
        if (root / marker).exists():
            checked(lambda: (assert_no_chooser(), next_step()))
            return False
        if time.monotonic() >= deadline:
            checked(lambda: (_ for _ in ()).throw(AssertionError((phrase, marker, feedback))))
            return False
        return True

    GLib.timeout_add(30, poll)


def assert_no_chooser():
    assert app.command_chooser is None


def start():
    app.session.preferences.hands_free = True
    app.session.recognizer.loaded = True
    app.session.microphone_problem = ""
    app.command_feedback = lambda message, **kwargs: feedback.append(message)
    assert fixtures["browser-one"].set_as_default_for_type("x-scheme-handler/http")
    assert fixtures["other-files"].set_as_default_for_type("inode/directory")
    assert fixtures["editor-one"].set_as_default_for_type("text/plain")
    assert fixtures["mail-one"].set_as_default_for_type("x-scheme-handler/mailto")
    terminal.set_string("exec", fixtures["terminal-two"].get_commandline())
    calculator.set_string("exec", fixtures["calculator-one"].get_commandline())
    launch("Open my browser", "browser-one", changed_browser)


def changed_browser():
    assert fixtures["browser-two"].set_as_default_for_type("x-scheme-handler/http")
    later(lambda: launch("Launch the default web browser", "browser-two", file_manager))


def file_manager():
    launch("Open files", "other-files", specific_nemo)


def specific_nemo():
    assert not (root / "nemo").exists()
    launch("Open Nemo", "nemo", configured_terminal)


def configured_terminal():
    assert not (root / "terminal-one").exists()
    launch("Open terminal", "terminal-two", changed_terminal)


def changed_terminal():
    terminal.set_string("exec", fixtures["terminal-one"].get_commandline())
    launch("Open console", "terminal-one", configured_calculator)


def configured_calculator():
    launch("Open calculator", "calculator-one", text_editor)


def text_editor():
    launch("Open text editor", "editor-one", mail)


def mail():
    launch("Open email", "mail-one", missing_default)


def missing_default():
    app.handsfree.stop.clear()
    with patch(
        "dictadesk.desktop.applications.Gio.AppInfo.get_default_for_type", return_value=None
    ):
        app.launch_command(("browser",), app.handsfree.generation, heard="Open browser")
    assert "No default web browser" in feedback[-1]
    assert_no_chooser()
    # Stale activations must not resolve or launch a new application.
    with patch("dictadesk.ui.application.Gio.AppInfo.get_all") as enumeration:
        app.launch_command(("browser",), app.handsfree.generation - 1)
        enumeration.assert_not_called()
    print(
        "PASS default applications: live MIME association changes, Cinnamon terminal/calculator settings, explicit Nemo despite Files label and another default manager, editor/mail, missing defaults, stale activation rejection"
    )
    app.request_quit()


with (
    patch("dictadesk.core.session.check"),
    patch("dictadesk.core.session.microphones", return_value=[]),
    patch("dictadesk.core.session.Session.load_available_model"),
    patch("dictadesk.core.handsfree.HandsFree.tick"),
):
    later(start)
    app.run(["dictadesk-default-applications-test"])
assert not errors, errors
