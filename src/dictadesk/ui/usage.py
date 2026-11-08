# SPDX-License-Identifier: GPL-3.0-only
"""Standalone help; every reference row comes from the executable registry."""

import threading

from ..core.commands import CATEGORIES, registry
from ..desktop.keyboard import Chord
from .gtk import Adw, Gdk, Gtk
from .widgets import text


def reference_rows(preferences, capabilities):
    values = {
        "volume_step": preferences.volume_step,
        "brightness_step": preferences.brightness_step,
    }
    return tuple(
        (
            spec,
            "\n".join(spec.examples or spec.phrases),
            spec.description.format(**values),
            capabilities.get(spec.capability, "Checking availability…"),
        )
        for spec in registry()
    )


class UsageWindow:
    def __init__(self, app):
        self.app = app
        self.closed = False
        self.signature = None
        self.capabilities = {}
        self.probing = False
        self.dialog = Adw.Window(
            application=app, title="Usage & Commands", default_width=740, default_height=760
        )
        monitor = Gdk.Display.get_default().get_monitors().get_item(0)
        if monitor:
            geometry = monitor.get_geometry()
            self.dialog.set_default_size(
                min(740, geometry.width - 64), min(760, geometry.height - 96)
            )
        self.dialog.set_size_request(320, 300)
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        close = self.close_button = Gtk.Button(label="Close")
        close.connect("clicked", lambda *_: self.dialog.close())
        header.pack_end(close)
        toolbar.add_top_bar(header)
        self.dialog.set_content(toolbar)
        self.content = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            margin_start=18,
            margin_end=18,
            margin_top=12,
            margin_bottom=18,
        )
        toolbar.set_content(
            Gtk.ScrolledWindow(
                hscrollbar_policy=Gtk.PolicyType.NEVER,
                child=Adw.Clamp(maximum_size=900, child=self.content),
            )
        )
        self.dialog.connect("close-request", self.close_requested)
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self.key_pressed)
        self.dialog.add_controller(keys)
        self.tables = {}
        self.row_ids = []

    def key_pressed(self, controller, keyval, keycode, state):
        if keyval == Gdk.KEY_Escape:
            self.dialog.close()
            return True
        return False

    def close_requested(self, *_):
        self.closed = True
        self.app.usage_window = None
        return False

    def present(self):
        self.refresh()
        self.dialog.present()
        if not self.probing:
            self.probing = True

            def probe():
                try:
                    capabilities = self.app.desktop_commands.probe()
                except Exception:
                    capabilities = {
                        spec.capability: "Availability could not be checked."
                        for spec in registry()
                    }

                def finish():
                    if not self.closed:
                        self.probing = False
                        if self.app.accessibility_problem:
                            capabilities["paste"] = self.app.accessibility_problem
                        self.capabilities = capabilities
                        self.refresh()

                self.app.dispatch(finish)

            threading.Thread(target=probe, daemon=True).start()

    def refresh(self):
        p = self.app.session.preferences
        signature = (
            p.shortcut,
            p.continuous_shortcut,
            p.wake_phrase,
            p.volume_step,
            p.brightness_step,
            p.spoken_punctuation,
            tuple(sorted(self.capabilities.items())),
        )
        if signature == self.signature:
            return
        self.signature = signature
        while child := self.content.get_first_child():
            self.content.remove(child)
        self.row_ids = []
        self.tables = {}
        title = text("How to Use DictaDesk")
        title.add_css_class("title-2")
        self.content.append(title)
        self.content.append(
            text(
                "DictaDesk runs in the system tray. It does not require a main application window."
            )
        )
        self.standard = self.section(
            f"Standard Dictation — {Chord.parse(p.shortcut)}",
            f"1. Click in the text field where you want to dictate.\n2. Press {Chord.parse(p.shortcut)}.\n3. Speak normally.\n4. Press {Chord.parse(p.shortcut)} again or Escape to finish.\n\n"
            "Text is inserted at pauses. DictaDesk remembers the original field when supported; after a focus change, remaining text waits until you finish, then returns to that field. If insertion fails, the text is copied to the clipboard with a notification. You can immediately record again.",
        )
        self.extended = self.section(
            f"Extended Dictation — {Chord.parse(p.continuous_shortcut)}",
            f"Press {Chord.parse(p.continuous_shortcut)} to start. "
            + (
                "With local Live Insertion enabled, agreed words are inserted while you speak; a provisional ending waits for more speech. "
                if p.backend == "local" and p.continuous_live
                else "Completed phrases are transcribed and inserted at pauses. "
            )
            + f"Press {Chord.parse(p.continuous_shortcut)} again or Escape to finish and insert the remaining text. "
            "Extended dictation uses the configured session length and does not stop at the standard silence timeout. It records dictation, never system commands.",
        )
        self.wake = self.section(
            "Command & Control — Wake Word",
            f"Enable Command & Control in Preferences.\n1. Say “{p.wake_phrase}”.\n2. Wait for the acknowledgment beep.\n3. Speak a command, such as “Volume down fifteen percent”.\n4. DictaDesk executes it and resumes wake-word listening.\n\n"
            "Listening is continuous only while enabled, using the local model. Pause between the wake word and command. Escape cancels an active command. Dictation takes priority. Use Pause Voice Commands for 30 Minutes in the tray to pause listening, or Resume Voice Commands Now to resume sooner. "
            + (
                "The acknowledgment sound is currently disabled in Preferences."
                if not p.command_sound
                else ""
            ),
        )
        heading = text("System Controls")
        heading.add_css_class("title-2")
        self.content.append(heading)
        self.content.append(
            text(
                f"Volume up/down defaults to {p.volume_step} percentage points; brightness defaults to {p.brightness_step}. “Volume up fifteen percent” increases the current level by 15 points. “Volume fifteen percent” sets it to 15%. Levels stay within 0–100%."
            )
        )
        self.content.append(
            text(
                "Unavailable controls are labeled below. Availability can change when devices or applications start or stop. Spoken formatting applies only in dictation with Spoken Punctuation enabled in Preferences."
            )
        )
        rows = reference_rows(p, self.capabilities)
        for category in CATEGORIES:
            label = text(category)
            label.add_css_class("heading")
            self.content.append(label)
            grid = Gtk.Grid(column_spacing=16, row_spacing=8, hexpand=True)
            grid.add_css_class("card")
            for column, value in enumerate(("Say", "Action")):
                cell = text(value, margin_start=8, margin_end=8, margin_top=8)
                cell.add_css_class("heading")
                grid.attach(cell, column, 0, 1, 1)
            row = 1
            for spec, say, action, unavailable in rows:
                if spec.category != category:
                    continue
                self.row_ids.append(spec.ident)
                phrase = text(say, hexpand=True, margin_start=8, margin_end=8, margin_bottom=8)
                phrase.set_max_width_chars(23)
                detail = text(
                    action + ("\nUnavailable: " + unavailable if unavailable else ""),
                    hexpand=True,
                    margin_start=8,
                    margin_end=8,
                    margin_bottom=8,
                )
                detail.set_max_width_chars(52)
                if unavailable:
                    detail.add_css_class("dim-label")
                grid.attach(phrase, 0, row, 1, 1)
                grid.attach(detail, 1, row, 1, 1)
                row += 1
            self.tables[category] = grid
            self.content.append(grid)

    def section(self, heading, body):
        title = text(heading)
        title.add_css_class("heading")
        self.content.append(title)
        label = text(body)
        self.content.append(label)
        return label
