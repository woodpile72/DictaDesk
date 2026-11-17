# SPDX-License-Identifier: GPL-3.0-only
# ruff: noqa: E402
"""Private chooser checks. All desktop actions and audio are fixtures."""

import os
from unittest.mock import patch

assert os.environ["HOME"].startswith("/tmp/dictadesk-test.")
from dictadesk.core.command_matching import suggest_commands, Suggestion
from dictadesk.core.voice_commands import choice_number
from dictadesk.ui.application import Application
from dictadesk.ui.gtk import GLib, Gdk

app = Application(isolated=True)
failures, actions = [], []


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

    GLib.timeout_add(300, run)


def prepare(text="volum up"):
    app.session.preferences.hands_free = True
    app.session.preferences.tts_enabled = True
    app.session.microphone_problem = ""
    app.handsfree.stop.clear()
    app.clarify_command(suggest_commands(text), app.handsfree.generation, heard=text)
    return app.command_chooser[0]


def start():
    window = prepare()
    assert window.listing.get_row_at_index(0).get_title() == "1. volume up"
    assert app.handsfree.choice[3] == "volume up?"
    assert not window.context.get_visible()
    assert window.hint.get_label() == "Say yes or no."
    assert app.handsfree.choice[4]
    assert not actions
    later(accept)


def execute(kind, arguments, generation, **kwargs):
    actions.append((kind, arguments))
    app.handsfree.complete(generation)


def accept():
    window, _ = app.command_chooser
    old = app.handsfree.choice
    old[1](choice_number("yes", confirmation=old[4]), "yes")
    assert window.cleaned and app.command_chooser is None
    assert actions == [("volume", ("up", 5))]
    old[1](1, "yes")
    assert len(actions) == 1
    prepare("lock screan")
    assert len(actions) == 1, "Consequential suggestion ran before confirmation"
    old = app.handsfree.choice
    old[1](choice_number("no", confirmation=True), "no")
    assert len(actions) == 1 and app.command_chooser is None
    prepare("lock screan")
    window, _ = app.command_chooser
    old = app.handsfree.choice
    assert window.key_pressed(None, Gdk.KEY_Escape, 0, 0)
    old[1](1, "yes")
    assert len(actions) == 1 and window.cleaned
    prepare()
    app.handsfree.choice[1]("timeout", "")
    assert app.command_chooser is None
    app.handsfree.stop.clear()
    app.clarify_command(
        [
            Suggestion("maximize window", "window", ("maximize",), 90),
            Suggestion("minimize window", "window", ("minimize",), 89),
        ],
        app.handsfree.generation,
        heard="fixture ambiguous command",
    )
    assert not app.handsfree.choice[4]
    assert "1. maximize window" in app.handsfree.choice[3]
    assert "2. minimize window" in app.handsfree.choice[3]
    app.handsfree.choice[1](2, "two")
    assert actions[-1] == ("window", ("minimize",))
    prepare()
    old = app.handsfree.choice
    app.session.preferences.backend = "openai"
    app.refresh()
    old[1](1, "yes")
    assert len(actions) == 2 and app.command_chooser is None
    print(
        "PASS fuzzy chooser: exact action payload, yes/no, numbers, Escape, timeout, stale callbacks, remote suspension"
    )
    app.request_quit()


with (
    patch("dictadesk.core.session.Session.scan_devices"),
    patch("dictadesk.core.session.Session.load_available_model"),
    patch("dictadesk.core.handsfree.HandsFree.tick"),
    patch("dictadesk.audio.speech.SpeechOutput.tick"),
    patch.object(app, "run_desktop_command", side_effect=execute),
):
    later(start)
    app.run(["command-matching-test"])
assert not failures, failures
