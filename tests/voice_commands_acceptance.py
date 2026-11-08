# SPDX-License-Identifier: GPL-3.0-only
# ruff: noqa: E402
"""Private GTK/desktop-entry checks: no real applications or microphone access."""

import os
import time
from pathlib import Path
from unittest.mock import Mock, patch

assert os.environ["HOME"].startswith("/tmp/dictadesk-test.")
from dictadesk.ui.application import Application
from dictadesk.core.voice_commands import choice_number
from dictadesk.ui.gtk import Adw, Gtk, Gio, GLib, Gdk
from types import SimpleNamespace

Adw.init()
Gtk.Settings.get_default().set_property("gtk-enable-animations", False)
app = Application(isolated=True)
failures = []
root = Path(os.environ["HOME"])
entries = []
heartbeats = []
GLib.timeout_add(10, lambda: heartbeats.append(time.monotonic()) or True)


def later(fn):
    def run():
        try:
            fn()
        except Exception as exc:
            import traceback

            traceback.print_exc()
            failures.append(exc)
            app.request_quit()
        return False

    GLib.timeout_add(400, run)


def start():
    app.session.microphone_problem = ""
    app.session.recognizer.loaded = True
    app.show_preferences()
    later(preferences)


def preferences():
    p = app.preferences_dialog
    assert p.voice_group.get_visible()
    assert p.voice_banner_group.get_visible()
    assert p.voice_banner.get_label() == "Experimental"
    assert not p.controls["hands_free"].get_active()
    assert p.wake_phrase.get_text() == "Computer"
    assert p.controls["command_sound"].get_active()
    assert "off" in p.voice_status.get_subtitle().lower()
    app.session.preferences.hands_free = True
    app.handsfree.state = "wake"
    p.refresh_backend()
    assert "Listening for “Computer”" in p.voice_status.get_subtitle()
    p.wake_phrase.set_text("Hello Desk")
    assert "Save to change" in p.voice_status.get_subtitle()
    assert "from “Computer” to “Hello Desk”" in p.voice_status.get_subtitle()
    app.handsfree.problem = "Microphone interrupted. Retrying in five seconds."
    p.refresh_backend()
    assert "Retrying" in p.voice_status.get_subtitle()
    app.handsfree.problem = ""
    app.handsfree.state = "off"
    app.session.preferences.hands_free = False
    p.wake_phrase.set_text("Computer")
    p.controls["backend"][0].set_selected(1)
    assert p.voice_group.get_visible()
    assert p.voice_banner_group.get_visible()
    assert p.commands_requirement.get_visible()
    p.controls["backend"][0].set_selected(0)
    p.wake_phrase.set_text("  !!! ")
    with patch.object(app, "save_preferences") as save:
        p.save_clicked()
        save.assert_not_called()
    assert "wake phrase" in p.validation_problem
    p.wake_phrase.set_text("Hello Desk")
    with patch("dictadesk.ui.preferences.downloaded", return_value=False):
        p.refresh_backend()
        assert p.controls["hands_free"].get_sensitive()
    p.refresh_backend()
    p.controls["hands_free"].set_active(True)

    def saved(prefs, key, close):
        assert prefs.hands_free and prefs.wake_phrase == "Hello Desk" and prefs.command_sound
        close()

    with patch.object(app, "save_preferences", side_effect=saved):
        p.save_clicked()
    app.session.preferences.hands_free = True
    app.handsfree.stop.clear()
    app.handsfree.state = "wake"
    updates = []
    app.tray = SimpleNamespace(
        ready=True, close=lambda: None, update=lambda *a: updates.append(a)
    )
    app.refresh()
    assert updates[-1][0] == "wake" and "Listening locally" in updates[-1][1]
    app.handsfree.state = "processing"
    app.refresh()
    assert updates[-1][0] == "wake" and "Checking" in updates[-1][1]
    app.handsfree.state = "command"
    with patch("dictadesk.ui.application.time.monotonic", return_value=0.9):
        app.refresh()
    assert updates[-1][0] == "listening" and "command" in updates[-1][1]
    count = len(updates)
    with patch("dictadesk.ui.application.time.monotonic", return_value=1.7):
        app.refresh()
    assert len(updates) == count, "Command listening still flashes the tray icon"
    app.session.phase = "listening"
    app.refresh()
    assert updates[-1][0] == "listening"
    app.session.phase = "idle"
    for n, name in enumerate(["Firefox Fixture", "Calibre Reader", "Calibre Editor"]):
        path = root / f"app{n}.desktop"
        path.write_text(
            f"[Desktop Entry]\nType=Application\nName={name}\nExec=/usr/bin/touch {root}/launched{n}\n"
        )
        entries.append(Gio.DesktopAppInfo.new_from_filename(str(path)))
    with patch("dictadesk.ui.application.Gio.AppInfo.get_all", return_value=entries):
        app.launch_command(("firefox", "fixture"), app.handsfree.generation)
    later(launched)


def launched():
    assert (root / "launched0").exists()
    app.handsfree.stop.clear()
    with patch("dictadesk.ui.application.Gio.AppInfo.get_all", return_value=entries):
        with patch.object(app, "command_feedback", wraps=app.command_feedback) as feedback:
            app.launch_command(("unknown",), app.handsfree.generation, heard="Open Unknown!")
            feedback.assert_called_once_with(
                "Application not found.", heard="Open Unknown!", query=("unknown",)
            )
        app.handsfree.stop.clear()
        app.launch_command(("firefoxx",), app.handsfree.generation, heard="Open Firefoxx")
        single, _ = app.command_chooser
        assert single.get_title() == "Did You Mean?"
        assert "Open Firefoxx" in single.context.get_label()
        assert single.key_pressed(None, Gdk.KEY_Escape, 0, 0)
        assert single.cleaned
        app.handsfree.stop.clear()
        app.launch_command(("calibre",), app.handsfree.generation)
    later(chooser)


def chooser():
    window, generation = app.command_chooser
    assert window.get_mapped()
    listing = window.listing
    row = listing.get_row_at_index(0)
    app.handsfree.suspend()
    app.refresh()
    assert app.command_chooser is None
    assert window.cleaned and not window.covers
    row.emit("activated")  # stale chooser signal must not launch
    assert not (root / "launched1").exists() and not (root / "launched2").exists()
    app.handsfree.stop.clear()
    with patch("dictadesk.ui.application.Gio.AppInfo.get_all", return_value=entries):
        app.launch_command(("calibre",), app.handsfree.generation)
    later(choose_valid)


def choose_valid():
    window, _ = app.command_chooser
    window.listing.get_row_at_index(0).emit("activated")
    later(pages_start)


def pages_start():
    app.handsfree.stop.clear()
    choices = []
    for n in range(1, 10):
        path = root / f"choice{n}.desktop"
        path.write_text(
            f"[Desktop Entry]\nType=Application\nName=Choice App {n}\nExec=/usr/bin/touch {root}/choice-launched{n}\n"
        )
        choices.append(Gio.DesktopAppInfo.new_from_filename(str(path)))
    with patch("dictadesk.ui.application.Gio.AppInfo.get_all", return_value=choices):
        app.launch_command(("choice",), app.handsfree.generation, heard="Open Choice")
    later(pages_choose)


def pages_choose():
    window, _ = app.command_chooser
    listing = window.listing
    assert listing.get_row_at_index(0).get_title() == "1. Choice App 1"
    assert listing.get_row_at_index(3).get_title() == "4. Choice App 4"
    assert listing.get_row_at_index(4).get_title() == "5. More matches"
    assert listing.get_row_at_index(5) is None
    adjustment = window.scroll.get_vadjustment()
    assert adjustment.get_upper() <= adjustment.get_page_size() + 1
    assert window.cancel.get_mapped()
    old = app.handsfree.choice
    app.handsfree.choice[1](choice_number("five"), "five")
    assert old[2].is_set()
    old[1](1, "one")  # old page cannot launch after paging
    assert listing.get_row_at_index(0).get_title() == "1. Choice App 5"
    app.handsfree.choice[1](choice_number("more"), "more")
    assert listing.get_row_at_index(0).get_title() == "1. Choice App 9"
    assert listing.get_row_at_index(1) is None
    app.handsfree.choice[1](5, "five")  # no next page, never launches something else
    assert app.command_chooser
    app.handsfree.choice[1](choice_number("number one"), "number one")
    later(pages_done)


def pages_done():
    assert (root / "choice-launched9").exists()
    assert not any((root / f"choice-launched{n}").exists() for n in range(1, 9))
    done()


def done():
    app.notifications_enabled = True
    note = Mock()
    with (
        patch.object(Gio.Notification, "new", return_value=note),
        patch.object(app, "send_notification") as send,
    ):
        app.command_feedback(
            "Application not found.", heard="Open Unknown!", query=("unknown",)
        )
        assert "Heard: “Open Unknown!”" in note.set_body.call_args.args[0]
        assert "Looking for application: “unknown”" in note.set_body.call_args.args[0]
        send.assert_called_once_with("voice-commands", note)
    app.notifications_enabled = False
    assert (root / "launched2").exists()  # sorted Editor before Reader
    assert not (root / "launched1").exists()
    app.session.preferences.backend = "openai"
    assert not app.handsfree.enabled
    intervals = sorted(b - a for a, b in zip(heartbeats, heartbeats[1:]))
    assert intervals and intervals[int(len(intervals) * 0.95)] < 0.1
    print(f"GTK heartbeat p95: {intervals[int(len(intervals) * 0.95)] * 1000:.1f} ms")
    print(
        "PASS Voice Commands GTK: local-only preferences, validation, steady listening indicators, desktop launch, ambiguity and stale chooser rejection"
    )
    app.request_quit()


with (
    patch("dictadesk.core.session.check"),
    patch("dictadesk.core.session.microphones", return_value=[]),
    patch("dictadesk.core.session.Session.load_available_model"),
    patch("dictadesk.core.handsfree.HandsFree.tick"),
    patch("dictadesk.ui.preferences.read_key", return_value=""),
    patch("dictadesk.ui.preferences.downloaded", return_value=True),
    patch(
        "dictadesk.ui.preferences.compute_capabilities",
        return_value={"cuda": False, "reason": "CPU test"},
    ),
):
    later(start)
    app.run(["voice-commands-test"])
assert not failures, failures
