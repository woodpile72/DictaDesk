# SPDX-License-Identifier: GPL-3.0-only
# ruff: noqa: E402
"""Login startup and single-instance activation on an isolated GTK desktop."""

import os
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch

assert os.environ["HOME"].startswith("/tmp/dictadesk-test.")
from dictadesk.ui.application import Application
from dictadesk.ui.gtk import GLib
from dictadesk.desktop.tray import Tray

missing_host = "--missing-host" in sys.argv
normal = "--normal" in sys.argv
app = Application(isolated=True)
maps = []
failures = []
child = None


def later(fn, milliseconds=150):
    def run():
        try:
            fn()
        except Exception as exc:
            import traceback

            traceback.print_exc()
            failures.append(exc)
            app.request_quit()
        return False

    GLib.timeout_add(milliseconds, run)


def begin():
    assert app.session is not None
    assert app.get_windows() == []
    if normal or missing_host:
        later(no_host, 5300)
        return
    app.tray = SimpleNamespace(ready=False, close=lambda: None, update=lambda *_: None)
    later(host_ready, 500)


def host_ready():
    assert app.get_windows() == []
    app.tray.ready = True
    later(hidden_after_deadline, 5000)


def no_host():
    assert app.get_windows() == []
    app.request_quit()


def launch(arguments, finished):
    global child
    code = "from dictadesk.ui.application import Application; import sys; raise SystemExit(Application(isolated=True).run(sys.argv))"
    child = subprocess.Popen([sys.executable, "-c", code, *arguments])

    def check():
        if child.poll() is None:
            later(check)
        else:
            assert child.returncode == 0
            finished()

    later(check)


def hidden_after_deadline():
    assert app.get_windows() == []
    launch(["--background"], repeated)


def repeated():
    assert app.get_windows() == []
    launch([], opened)


def opened():
    assert app.get_windows() == []
    app.activate_action("show", None)
    assert app.preferences_dialog and not hasattr(app, "window")
    app.preferences_dialog.dialog.close()
    Tray.set_ready(SimpleNamespace(ready=True, app=app), False)
    assert not app.get_windows(), "Host loss created an unsolicited window"
    app.request_quit()


with (
    patch("dictadesk.core.session.Session.scan_devices"),
    patch("dictadesk.core.session.Session.load_available_model") as load,
):
    later(begin)
    app.run(["startup-test", *([] if normal else ["--background"])])
    load.assert_called_once()
assert not failures, failures
print(
    "PASS",
    "normal launch"
    if normal
    else "missing host without window"
    if missing_host
    else "hidden startup, delayed host, repeated background, normal activation, Preferences and host loss",
)
