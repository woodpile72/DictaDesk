# SPDX-License-Identifier: GPL-3.0-only
# ruff: noqa: E402
"""Real Firefox fields/navigation, offline and isolated from the user's profile."""

import os
from pathlib import Path
import subprocess
import time
from unittest.mock import patch

assert os.environ["HOME"].startswith("/tmp/dictadesk-test.")
from Xlib import X, Xatom, XK, display
from Xlib.ext import xtest
from dictadesk.desktop.destination import DestinationTracker
from dictadesk.ui.application import Application
from dictadesk.ui.gtk import GLib

app = Application(isolated=True)
connection, wm = display.Display(), display.Display()
wm.screen().root.change_attributes(event_mask=X.SubstructureNotifyMask)
wm.flush()
processes, failures = [], []
root = Path(os.environ["HOME"])
profile = root / "firefox-profile"
profile.mkdir()
(profile / "user.js").write_text("""user_pref("accessibility.force_disabled", -1);
user_pref("browser.shell.checkDefaultBrowser", false);
user_pref("browser.aboutwelcome.enabled", false);
user_pref("browser.startup.homepage_override.mstone", "ignore");
user_pref("datareporting.policy.dataSubmissionEnabled", false);
user_pref("toolkit.telemetry.enabled", false);
user_pref("browser.newtabpage.enabled", false);
""")
page = root / "fields.html"
page.write_text("""<!doctype html><title>DictaDesk browser fixture</title>
<input aria-label="Original browser field" value="before selected after" autofocus>
<input aria-label="Other browser field" value="Unrelated browser field">
<button>Not a text field</button>""")


class Keyboard:
    down = set()
    errors = {}
    connection = connection

    def set_escape(self, enabled):
        pass

    def modifiers_down(self):
        return False

    def poll(self):
        while wm.pending_events():
            event = wm.next_event()
            if event.type == X.ClientMessage and event.client_type == wm.intern_atom(
                "_NET_ACTIVE_WINDOW"
            ):
                focus(event.window.id)

    def close(self):
        pass


def focus(ident):
    connection.set_input_focus(ident, X.RevertToParent, X.CurrentTime)
    connection.screen().root.change_property(
        connection.intern_atom("_NET_ACTIVE_WINDOW"), Xatom.WINDOW, 32, [ident]
    )
    connection.sync()


def later(fn, delay=400):
    def run():
        try:
            fn()
        except Exception as exc:
            import traceback

            traceback.print_exc()
            failures.append(exc)
            app.request_quit()
        return False

    GLib.timeout_add(delay, run)


def until(predicate, callback):
    deadline = time.monotonic() + 15

    def check():
        assert time.monotonic() < deadline, "Browser operation timed out"
        if predicate():
            callback()
        else:
            later(check, 75)

    later(check, 75)


def text(obj):
    return app.destinations.api.Text.get_text(obj, 0, -1)


def start():
    app.keyboard = Keyboard()
    app.destinations = DestinationTracker(connection)
    env = {
        **os.environ,
        "MOZ_ACCESSIBILITY_ATSPI_ENABLED": "1",
        "NO_AT_BRIDGE": "0",
        "GTK_A11Y": "always",
    }
    processes.append(
        subprocess.Popen(
            ["firefox", "--no-remote", "--offline", "--profile", str(profile), page.as_uri()],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=(root / "firefox.log").open("w"),
        )
    )
    processes.append(
        subprocess.Popen(
            ["/usr/bin/python3", str(Path(__file__).with_name("destination_fixture.py"))],
            env=env,
            stdout=subprocess.DEVNULL,
            stdin=subprocess.PIPE,
        )
    )
    later(discover, 6000)


def discover():
    global browser_xid, other_xid, first, second
    windows = {}
    for w in connection.screen().root.query_tree().children:
        prop = w.get_full_property(connection.intern_atom("_NET_WM_NAME"), X.AnyPropertyType)
        name = prop.value.decode() if prop is not None else w.get_wm_name()
        windows[name] = w.id
    matches = [
        ident for name, ident in windows.items() if name and "DictaDesk browser fixture" in name
    ]
    assert matches, (
        windows,
        [(p.pid, p.poll()) for p in processes],
        (root / "firefox.log").read_text(),
    )
    browser_xid = matches[0]
    other_xid = windows["DictaDesk other fixture"]
    focus(browser_xid)
    fields = {}
    deadline = time.monotonic() + 5

    def visit(obj):
        assert time.monotonic() < deadline, "Browser accessibility discovery timed out"
        name = obj.get_name()
        if name in ("Original browser field", "Other browser field"):
            fields[name] = obj
        for i in range(obj.get_child_count()):
            visit(obj.get_child_at_index(i))

    visit(app.destinations.api.get_desktop(0))
    first, second = fields["Original browser field"], fields["Other browser field"]
    first.get_component_iface().grab_focus()
    later(cross_application)


def cross_application():
    global target
    first.set_caret_offset(7)
    first.add_selection(7, 15)
    target = app.destinations.capture()
    assert target.control == first, (target, first.get_state_set().get_states())
    assert app.destinations.valid(target)
    focus(other_xid)
    later(lambda: deliver("FIREFOX", target, cross_done))


def deliver(value, target, callback):
    c = app.session
    c.phase = "idle"
    c.pending_text = ""
    c._begin("transcribing")
    c.result.replace(value, [])
    app.deliver(value, target)
    until(lambda: not c.busy, callback)


def cross_done():
    assert text(first) == "before FIREFOX after", (text(first), app.session.notice)
    assert text(second) == "Unrelated browser field"
    assert connection.get_input_focus().focus.id == other_xid
    focus(browser_xid)
    first.get_component_iface().grab_focus()
    later(same_window)


def same_window():
    global target
    first.set_caret_offset(len(text(first)))
    target = app.destinations.capture()
    second.get_component_iface().grab_focus()
    later(lambda: deliver(" SAME FIELD", target, same_done))


def same_done():
    assert text(first).endswith(" SAME FIELD"), (text(first), app.session.notice)
    assert text(second) == "Unrelated browser field"
    first.get_component_iface().grab_focus()
    later(navigate)


def navigate():
    global target
    target = app.destinations.capture()
    # Reload the same document with identical starting text; stale AT-SPI
    # references must never be resolved by name, index or matching contents.
    control = connection.keysym_to_keycode(XK.string_to_keysym("Control_L"))
    key = connection.keysym_to_keycode(XK.string_to_keysym("r"))
    for kind, code in (
        (X.KeyPress, control),
        (X.KeyPress, key),
        (X.KeyRelease, key),
        (X.KeyRelease, control),
    ):
        xtest.fake_input(connection, kind, code)
    connection.sync()
    later(lambda: deliver("STALE PAGE", target, navigated), 1800)


def navigated():
    assert app.session.pending_text == "STALE PAGE", app.session.notice
    assert not app.destinations.valid(target)
    assert app.session.result.text == "STALE PAGE"
    print(
        "PASS real offline Firefox: original field/selection across application switch, same-window field restoration, return focus, stale browser page rejected and transcript retained"
    )
    app.request_quit()


with (
    patch("dictadesk.core.session.check"),
    patch("dictadesk.core.session.microphones", return_value=[]),
):
    later(start)
    try:
        app.run(["dictadesk-firefox-test"])
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        connection.close()
        wm.close()
assert not failures, failures
