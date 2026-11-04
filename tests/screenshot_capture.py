# SPDX-License-Identifier: GPL-3.0-only
# ruff: noqa: E402
"""Documentation screenshots with synthetic state; run through scripts/test-ui."""

import os
from pathlib import Path
import subprocess
from unittest.mock import patch

assert os.environ["HOME"].startswith("/tmp/dictadesk-test.")
from dictadesk.ui.application import Application
from dictadesk.ui.gtk import Adw, Gtk, GLib
from gi.repository import GdkX11

Adw.init()
Gtk.Settings.get_default().set_property("gtk-enable-animations", False)
Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
app = Application(isolated=True)
out = Path(__file__).resolve().parents[1] / "media/screenshots"
out.mkdir(parents=True, exist_ok=True)
errors = []


class Worker:
    loaded = True

    def close(self):
        self.loaded = False


def later(fn):
    def call():
        try:
            fn()
        except Exception as exc:
            import traceback

            traceback.print_exc()
            errors.append(exc)
            app.session.phase = "idle"
            app.request_quit()
        return False

    GLib.timeout_add(600, call)


def shot(name):
    xid = GdkX11.X11Surface.get_xid(app.dialog_parent().get_surface())
    subprocess.run(["import", "-window", str(xid), str(out / (name + ".png"))], check=True)


def start():
    c = app.session
    c.recognizer = Worker()
    c.microphone_problem = ""
    c.notice = ""
    app.refresh()
    later(main)


def main():
    shot("main-window")
    app.window.show_inline_transcript()
    c = app.session
    c.phase, c.in_window = "listening", True
    c.inline_text = "Here is a short example of dictating directly into DictaDesk. When I finish, the complete transcript is copied to the clipboard."
    app.refresh()
    later(button)


def button():
    shot("button-dictation")
    c = app.session
    c.phase, c.in_window = "idle", False
    app.window.hide_inline_transcript()
    app.refresh()
    app.show_preferences()
    later(local)


def local():
    shot("preferences-local")
    app.preferences_dialog.show_model_guide()
    later(model_guide)


def model_guide():
    shot("model-guide")
    app.preferences_dialog.model_guide.close()
    app.preferences_dialog.controls["backend"][0].set_selected(1)
    later(remote)


def remote():
    shot("preferences-remote")
    app.preferences_dialog.controls["backend"][0].set_selected(0)
    app.session.set_problem("loading", "The selected model could not be opened.")
    app.refresh()
    later(problem)


def problem():
    shot("preferences-problem")
    app.preferences_dialog.dialog.close()
    app.session.problem = None
    app.session.notice = ""
    app.window.set_default_size(749, 600)
    app.refresh()
    app.show_about()
    later(done)


def done():
    shot("about")
    print("Saved seven documentation screenshots with synthetic content.")
    app.request_quit()


with (
    patch("dictadesk.core.session.check"),
    patch("dictadesk.core.session.microphones", return_value=[]),
    patch("dictadesk.ui.preferences.read_key", return_value=""),
    patch("dictadesk.ui.preferences.downloaded", return_value=True),
    patch(
        "dictadesk.ui.preferences.cache_info",
        return_value={"exists": True, "bytes": 640 * 1024**2, "unused_days": 0},
    ),
    patch(
        "dictadesk.ui.preferences.compute_capabilities",
        return_value={"cuda": False, "reason": "This package uses CPU processing."},
    ),
):
    later(start)
    app.run(["dictadesk-screenshots"])
assert not errors, errors
