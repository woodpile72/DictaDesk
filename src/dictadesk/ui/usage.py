# SPDX-License-Identifier: GPL-3.0-only
"""Three independent help pages; command tables come from the live registry."""

import threading

from ..core.commands import CATEGORIES, registry
from ..core.tts import ENGINES, VOICES
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
            application=app, title="DictaDesk Help", default_width=740, default_height=760
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
        self.close_button = Gtk.Button(label="Close")
        self.close_button.connect("clicked", lambda *_: self.dialog.close())
        header.pack_end(self.close_button)
        toolbar.add_top_bar(header)
        self.dialog.set_content(toolbar)
        self.stack = Adw.ViewStack()
        self.pages = {}
        for name, title, icon in (
            ("dictation", "Dictation", "audio-input-microphone-symbolic"),
            ("commands", "Command & Control", "preferences-system-symbolic"),
            ("tts", "Talk Back", "audio-speakers-symbolic"),
        ):
            content = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL,
                spacing=12,
                margin_start=18,
                margin_end=18,
                margin_top=12,
                margin_bottom=18,
            )
            self.pages[name] = content
            scroll = Gtk.ScrolledWindow(
                hscrollbar_policy=Gtk.PolicyType.NEVER,
                child=Adw.Clamp(maximum_size=900, child=content),
            )
            self.stack.add_titled_with_icon(scroll, name, title, icon)
        self.content = self.pages["dictation"]
        toolbar.set_content(self.stack)
        switcher = Adw.ViewSwitcherBar()
        switcher.set_stack(self.stack)
        switcher.set_reveal(True)
        toolbar.add_bottom_bar(switcher)
        self.dialog.connect("close-request", self.close_requested)
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self.key_pressed)
        self.dialog.add_controller(keys)
        self.tables, self.row_ids = {}, []

    def key_pressed(self, controller, keyval, keycode, state):
        if keyval == Gdk.KEY_Escape:
            self.dialog.close()
            return True
        return False

    def close_requested(self, *_):
        self.closed = True
        self.app.usage_window = None
        return False

    def present(self, page="dictation"):
        self.refresh()
        self.stack.set_visible_child_name(page if page in self.pages else "dictation")
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
            p.cancel_shortcut,
            p.paste_shortcut,
            p.backend,
            p.continuous_live,
            p.continuous_minutes,
            p.wake_phrase,
            p.volume_step,
            p.brightness_step,
            p.spoken_punctuation,
            p.command_sound,
            p.hands_free,
            p.tts_enabled,
            p.tts_engine,
            p.tts_model,
            p.tts_phrase,
            tuple(sorted(self.capabilities.items())),
        )
        if signature == self.signature:
            return
        self.signature = signature
        for parent in self.pages.values():
            while child := parent.get_first_child():
                parent.remove(child)
        self.row_ids, self.tables = [], {}
        parent = self.pages["dictation"]
        self.heading(parent, "Dictation")
        parent.append(
            text(
                "DictaDesk is a tray utility. Dictation is its primary function; it needs no main application window and never executes system commands."
            )
        )
        self.standard = self.section(
            parent,
            f"Standard Dictation — {Chord.parse(p.shortcut)}",
            f"1. Click in a text field.\n2. Press {Chord.parse(p.shortcut)}.\n3. Speak normally.\n4. Press {Chord.parse(p.shortcut)} again or Escape to finish.\n\n"
            "Text is inserted at pauses. DictaDesk remembers the original field when supported, even if focus changes. At completion, remaining text returns to that field. Failed insertion copies the transcript to the clipboard with a notification; you can immediately record again.",
        )
        self.extended = self.section(
            parent,
            f"Extended Dictation — {Chord.parse(p.continuous_shortcut)}",
            f"Press {Chord.parse(p.continuous_shortcut)} to start. "
            + (
                "With local Live Insertion enabled, agreed words are inserted while you speak; a provisional ending waits for more speech. "
                if p.backend == "local" and p.continuous_live
                else "Completed phrases are transcribed and inserted at pauses. "
            )
            + f"Press {Chord.parse(p.continuous_shortcut)} again or Escape to finish. "
            f"Extended dictation has a {p.continuous_minutes}-minute session limit, without the standard silence timeout. It records dictation, never system commands.",
        )
        parent.append(
            text(
                f"{Chord.parse(p.cancel_shortcut)} cancels recording. "
                f"{Chord.parse(p.paste_shortcut)} explicitly pastes the last completed dictation into the verified field you choose."
            )
        )
        parent.append(
            text(
                "Spoken formatting applies only when Spoken Punctuation is enabled in Preferences."
            )
        )
        self.table(parent, "Dictation Formatting", p)
        parent = self.pages["commands"]
        self.heading(parent, "Command & Control — Experimental")
        acknowledgment = (
            f"the spoken acknowledgment “{p.tts_phrase}”"
            if p.tts_enabled
            else "the acknowledgment beep"
            if p.command_sound
            else "the tray listening indicator"
        )
        self.wake = self.section(
            parent,
            "Optional local voice commands",
            f"Enable Command & Control in Preferences. If required files are missing, guided setup asks you to choose the local engine and model, download it, then Enable and Save. It listens continuously only while enabled.\n\n"
            f"One phrase: say “{p.wake_phrase}, lock screen” or “{p.wake_phrase}, volume down fifteen percent”, then pause. DictaDesk executes one complete recognized command directly, without waiting for an acknowledgment.\n\n"
            f"Two steps: say “{p.wake_phrase}” alone, wait for {acknowledgment}, then speak a supported command.\n\n"
            "Say the wake phrase on its own if using the two-step workflow. Small recognition errors can be tolerated in an isolated wake phrase; combined commands require the actual wake phrase. Escape cancels an active command. Dictation always takes priority. Unrecognized, incomplete or combined multiple commands never execute. Pause Voice Commands in the tray pauses listening for 30 minutes; Resume Voice Commands Now resumes sooner.",
        )
        self.heading(parent, "System Controls")
        parent.append(
            text(
                f"Volume up/down defaults to {p.volume_step} percentage points; brightness defaults to {p.brightness_step}. “Volume up fifteen percent” increases the current level by 15 points. “Volume fifteen percent” sets it to 15%. Levels stay within 0–100%. Unavailable controls are labeled below."
            )
        )
        for category in CATEGORIES:
            if category != "Dictation Formatting":
                self.table(parent, category, p)
        parent = self.pages["tts"]
        self.heading(parent, "Talk Back — Experimental")
        self.tts = self.section(
            parent,
            "Optional spoken acknowledgment",
            f"Off by default. Current setting: {'enabled' if p.tts_enabled else 'disabled'}. The saved acknowledgment is “{p.tts_phrase}”.\n\n"
            "1. Open Preferences and select the Talk Back tab.\n2. Turn on spoken acknowledgment. If files are missing, guided setup asks you to choose an engine and voice, then download Piper's optional files or install eSpeak NG through the system installer. Choose Check Again after installing eSpeak; no restart is needed.\n3. Enable when setup shows Ready. Test Voice previews your choices without saving.\n4. Save Preferences to apply them.\n\n"
            f"When you say “{p.wake_phrase}” alone, DictaDesk speaks “{p.tts_phrase}” instead of the beep before listening for your command. A combined wake phrase and command executes directly without speech delaying it. The configured beep is a fallback if synthesis fails.\n\n"
            "Downloads happen only when you choose Download. Synthesis and playback stay local. No speech model or TTS process is needed for ordinary dictation. Starting dictation or Stop Preview interrupts a preview. Remove TTS Downloads removes only optional speech files, leaving dictation models intact.",
        )
        self.section(
            parent, "Saved voice", f"{ENGINES[p.tts_engine]}\n{VOICES[p.tts_model].name}"
        )
        for engine, title in ENGINES.items():
            self.section(
                parent,
                title,
                "\n\n".join(
                    voice.name + "\n" + voice.description
                    for voice in VOICES.values()
                    if voice.engine == engine
                ),
            )
        order = {
            spec.ident: index
            for index, spec in enumerate(
                spec
                for category in CATEGORIES
                for spec in registry()
                if spec.category == category
            )
        }
        self.row_ids.sort(key=order.__getitem__)

    @staticmethod
    def heading(parent, value):
        label = text(value)
        label.add_css_class("title-2")
        parent.append(label)

    @staticmethod
    def section(parent, heading, body):
        title = text(heading)
        title.add_css_class("heading")
        parent.append(title)
        label = text(body)
        parent.append(label)
        return label

    def table(self, parent, category, preferences):
        label = text(category)
        label.add_css_class("heading")
        parent.append(label)
        grid = Gtk.Grid(column_spacing=16, row_spacing=8, hexpand=True)
        grid.add_css_class("card")
        for column, value in enumerate(("Say", "Action")):
            cell = text(value, margin_start=8, margin_end=8, margin_top=8)
            cell.add_css_class("heading")
            grid.attach(cell, column, 0, 1, 1)
        row = 1
        for spec, say, action, unavailable in reference_rows(preferences, self.capabilities):
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
        parent.append(grid)
