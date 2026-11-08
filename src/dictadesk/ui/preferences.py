# SPDX-License-Identifier: GPL-3.0-only
"""Transactional preferences; closing or Cancel discards unsaved edits."""

import threading
import os
from dataclasses import asdict

from ..audio.service import compute_capabilities
from ..core.settings import Preferences
from ..core.models import (
    LOCAL_MODELS,
    REMOTE_MODELS,
    UNUSED_DAYS,
    cache_info,
    downloaded,
    format_size,
)
from ..core.credentials import read_key
from ..core.connection import test_connection
from ..desktop.keyboard import Chord
from .gtk import Adw, Gdk, Gtk
from .widgets import text


class PreferencesDialog:
    def __init__(self, app):
        self.app = app
        self.dialog = Adw.Window(
            title="Preferences",
            application=app,
            resizable=True,
        )
        width, height = 760, 820
        display = Gdk.Display.get_default()
        monitor = display.get_monitors().get_item(0)
        if monitor:
            geometry = monitor.get_geometry()
            width = min(width, max(320, geometry.width - 64))
            height = min(height, max(300, geometry.height - 96))
        self.dialog.set_default_size(width, height)
        self.dialog.set_size_request(320, 300)
        toolbar = Adw.ToolbarView()
        self.dialog.set_content(toolbar)
        header = Adw.HeaderBar(show_start_title_buttons=False, show_end_title_buttons=False)
        toolbar.add_top_bar(header)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self.dialog.close())
        header.pack_start(cancel)
        self.save = Gtk.Button(label="Save")
        self.save.add_css_class("suggested-action")
        self.save.connect("clicked", self.save_clicked)
        header.pack_end(self.save)
        page = Adw.PreferencesPage()
        toolbar.set_content(page)
        self.values = asdict(app.session.preferences)
        self.secret = None
        self.closed_flag = False
        self.compute_cancel = threading.Event()
        self.compute_capability = None
        self.model_guide = None
        self.connection_generation = 0
        self.connection_busy = False
        self.storage_busy = False
        self.storage_phase = None
        self.storage_info = None
        self.controls = {}
        self.validation_problem = ""
        self.model_prompt = None
        self.shortcut_rows = {}
        self.setup_group = Adw.PreferencesGroup(
            title="Start Here — Download a Speech Model", visible=False
        )
        self.setup_row = Adw.ActionRow(use_markup=False)
        self.setup_download = Gtk.Button(label="Download Model", valign=Gtk.Align.CENTER)
        self.setup_download.add_css_class("suggested-action")
        self.setup_download.connect("clicked", self.download_model)
        self.setup_row.add_suffix(self.setup_download)
        self.setup_group.add(self.setup_row)
        page.add(self.setup_group)
        usage = Adw.PreferencesGroup(title="Using DictaDesk")
        self.usage = text()
        self.usage.set_margin_top(4)
        self.usage.set_margin_bottom(4)
        usage.add(self.usage)
        usage.add(
            Gtk.Button(
                label="Usage & Commands…", action_name="app.usage", halign=Gtk.Align.START
            )
        )
        page.add(usage)
        self.update_usage()
        self.attention_group = Adw.PreferencesGroup(title="Needs Attention", visible=False)
        self.attention = Adw.ActionRow(use_markup=False)
        self.attention_group.add(self.attention)
        self.recovery = Gtk.Button(
            label="Back Up Settings and Start Fresh", action_name="app.recover"
        )
        self.attention_group.add(self.recovery)
        page.add(self.attention_group)
        shortcuts = Adw.PreferencesGroup(
            title="Keyboard",
            description="Press once to start; press again or Escape to finish. Record inserts at pauses; Continuous Dictation supports live insertion.",
        )
        page.add(shortcuts)
        self.shortcut_status = Adw.ActionRow(
            title="Shortcut unavailable", use_markup=False, visible=False
        )
        self.shortcut_status.add_prefix(Gtk.Image(icon_name="dialog-warning-symbolic"))
        shortcuts.add(self.shortcut_status)
        for key, title in (
            ("shortcut", "Record"),
            ("continuous_shortcut", "Toggle Continuous Dictation"),
            ("cancel_shortcut", "Cancel Recording"),
            ("paste_shortcut", "Paste Last Dictation"),
        ):
            row = Adw.ActionRow(title=title, subtitle=str(Chord.parse(self.values[key])))
            button = Gtk.Button(label="Change…", valign=Gtk.Align.CENTER)
            button.connect("clicked", lambda _, key=key: self.capture_shortcut(key))
            row.add_suffix(button)
            shortcuts.add(row)
            self.shortcut_rows[key] = row
        inputs = Adw.PreferencesGroup(
            title="Microphone", description="System Default follows the desktop sound settings."
        )
        page.add(inputs)
        self.input = Adw.ComboRow(title="Input", use_subtitle=True)
        inputs.add(self.input)
        self.microphone_status = Adw.ActionRow(
            title="Microphone unavailable", use_markup=False, visible=False
        )
        self.microphone_status.add_prefix(Gtk.Image(icon_name="dialog-warning-symbolic"))
        inputs.add(self.microphone_status)
        refresh = Gtk.Button(
            icon_name="view-refresh-symbolic",
            tooltip_text="Refresh Microphones",
            valign=Gtk.Align.CENTER,
        )
        refresh.connect("clicked", lambda *_: app.session.scan_devices())
        self.input.add_suffix(refresh)
        self.device_values = []
        self.device_snapshot = None
        self.populate_devices()
        test = Adw.ActionRow(
            title="Test Microphone",
            subtitle="Try the microphone and transcription options selected here, before saving.",
            activatable=True,
        )
        test.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        test.connect("activated", lambda *_: self.app.show_microphone_test())
        inputs.add(test)
        speech = Adw.PreferencesGroup(
            title="Transcription", description="Choose where your speech is processed."
        )
        page.add(speech)
        self.combo(
            speech,
            "backend",
            "Transcription",
            [("local", "Local"), ("openai", "Remote")],
        )
        timing = Adw.PreferencesGroup(
            title="Dictation Timing",
            description="Short pauses show text; longer pauses finalize sentence endings.",
        )
        page.add(timing)
        self.local_group = local = Adw.PreferencesGroup(
            title="Local Transcription",
            description="No account is needed. Download a model once, then dictate offline. Switching to Remote keeps downloads until you remove them.",
        )
        page.add(local)
        self.combo(
            local,
            "local_model",
            "Model",
            [(key, value["name"]) for key, value in LOCAL_MODELS.items()],
        )
        self.model_description = Adw.ActionRow(title="About This Model")
        local.add(self.model_description)
        self.model_help = Adw.ActionRow(title="How do I choose a model?", activatable=True)
        self.model_help.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        self.model_help.connect("activated", lambda *_: self.show_model_guide())
        local.add(self.model_help)
        self.combo(local, "device", "Compute Device", [("cpu", "CPU"), ("cuda", "NVIDIA CUDA")])
        self.controls["device"][0].set_sensitive(False)
        self.compute_status = Adw.ActionRow(
            title="CUDA Availability", subtitle="Checking this installation…"
        )
        local.add(self.compute_status)
        self.combo(
            local, "quantization", "Model Precision", [("int8", "Int8"), ("float32", "Float32")]
        )
        self.download_row = Adw.ActionRow(title="Model Download")
        self.download_button = Gtk.Button(label="Download", valign=Gtk.Align.CENTER)
        self.download_button.connect("clicked", self.download_model)
        self.download_row.add_suffix(self.download_button)
        local.add(self.download_row)
        self.selected_remove_row = Adw.ActionRow(title="Remove Selected Model")
        self.selected_remove_button = Gtk.Button(label="Remove…", valign=Gtk.Align.CENTER)
        self.selected_remove_button.connect(
            "clicked", lambda *_: self.app.remove_models(self.selected_model())
        )
        self.selected_remove_row.add_suffix(self.selected_remove_button)
        local.add(self.selected_remove_row)
        self.storage_row = Adw.ActionRow(title="Remove Downloaded Models")
        self.remove_button = Gtk.Button(label="Remove…", valign=Gtk.Align.CENTER)
        self.remove_button.connect("clicked", lambda *_: self.app.remove_models())
        self.storage_row.add_suffix(self.remove_button)
        local.add(self.storage_row)
        self.model_row = Adw.ActionRow(title="Model in Memory")
        self.model_button = Gtk.Button(label="Load", valign=Gtk.Align.CENTER)
        self.model_button.connect("clicked", self.model_action)
        self.model_row.add_suffix(self.model_button)
        local.add(self.model_row)
        self.repair = Adw.ActionRow(
            title="Repair Model…",
            subtitle="Replace a damaged download; keep the previous model as a backup.",
            activatable=True,
        )
        self.repair.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        self.repair.connect(
            "activated",
            lambda *_: self.app.confirm_download(
                self.selected_precision(), self.selected_model()
            ),
        )
        local.add(self.repair)
        self.spin(
            local,
            "idle_seconds",
            "Unload After (Seconds)",
            0,
            86400,
            "Use 0 to keep the model in memory.",
        )
        preview = Adw.SwitchRow(
            title="Preview at Pauses",
            subtitle="Nothing is inserted until recording finishes.",
            active=self.values["preview_pauses"],
        )
        local.add(preview)
        self.controls["preview_pauses"] = preview
        live = Adw.SwitchRow(
            title="Live Continuous Dictation",
            subtitle="Preview speech and insert agreed words while speaking. Turn off to insert completed phrases at pauses. Uses more local processing.",
            active=self.values["continuous_live"],
        )
        local.add(live)
        self.controls["continuous_live"] = live
        for key, title, low, high, step, digits in [
            ("pause_seconds", "Text Update Pause (Seconds)", 0.3, 5, 0.1, 1),
            ("sentence_pause_seconds", "Sentence Ending Pause (Seconds)", 1, 10, 0.1, 1),
            ("silence_stop_seconds", "Stop After Silence (Seconds)", 0, 600, 1, 0),
            ("silence_threshold", "Silence Threshold", 0.001, 0.2, 0.001, 3),
        ]:
            row = Adw.SpinRow.new_with_range(low, high, step)
            row.set_title(title)
            if key == "pause_seconds":
                row.set_subtitle("Show text after a short pause without ending the sentence.")
            elif key == "sentence_pause_seconds":
                row.set_subtitle(
                    "Default: 4 seconds. Lower this for shorter gaps between sentences; "
                    "raise it for thinking pauses. Text Update Pause also sets the minimum wait."
                )
            elif key == "silence_stop_seconds":
                row.set_subtitle(
                    "Stops ordinary shortcut dictation after silence. Default: 20 seconds; "
                    "0 means Off. Maximum recording length still applies."
                )
                row.set_numeric(False)
                row.connect("output", self.format_silence_stop)
            row.set_digits(digits)
            row.set_value(self.values[key])
            if key != "silence_threshold":
                timing.add(row)
            self.controls[key] = row
        self.voice_banner_group = Adw.PreferencesGroup()
        self.voice_banner = Gtk.Label(label="Experimental", wrap=True, hexpand=True)
        self.voice_banner.add_css_class("voice-experimental")
        self.voice_banner_css = Gtk.CssProvider()
        self.voice_banner_css.load_from_data(
            b".voice-experimental { background-color: #b71c1c; color: #ffffff; "
            b"font-weight: 700; padding: 10px 12px; border-radius: 8px; }"
        )
        self.voice_banner.get_style_context().add_provider(
            self.voice_banner_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        self.voice_banner_group.add(self.voice_banner)
        page.add(self.voice_banner_group)
        self.voice_group = Adw.PreferencesGroup(
            title="Command &amp; Control",
            description="Keeps the microphone listening locally. Say the configured wake phrase, wait for the acknowledgment, then speak a supported command. Usage &amp; Commands lists the available controls. Dictation takes priority.",
        )
        page.add(self.voice_group)
        hands_free = Adw.SwitchRow(
            title="Enable hands-free commands", active=self.values["hands_free"]
        )
        self.voice_group.add(hands_free)
        self.controls["hands_free"] = hands_free
        self.wake_phrase = Adw.EntryRow(title="Wake phrase", text=self.values["wake_phrase"])
        self.wake_phrase.connect("notify::text", lambda *_: self.refresh_backend())
        self.voice_group.add(self.wake_phrase)
        sound = Adw.SwitchRow(
            title="Play acknowledgment sound", active=self.values["command_sound"]
        )
        self.voice_group.add(sound)
        self.controls["command_sound"] = sound
        self.voice_status = Adw.ActionRow(title="Local listening", use_markup=False)
        self.voice_group.add(self.voice_status)
        self.spin(self.voice_group, "volume_step", "Default Volume Adjustment (%)", 1, 100)
        self.spin(
            self.voice_group, "brightness_step", "Default Brightness Adjustment (%)", 1, 100
        )
        self.remote_group = remote = Adw.PreferencesGroup(
            title="Remote Transcription",
            description="Requires an API account and key. Audio leaves this computer and is sent to the provider. API charges may apply.",
        )
        page.add(remote)
        remote.add(Adw.ActionRow(title="Provider", subtitle="OpenAI"))
        self.remote_model = Adw.ActionRow(
            title="Service Model",
            subtitle=REMOTE_MODELS[self.values["cloud_model"]],
        )
        remote.add(self.remote_model)
        self.language = Adw.EntryRow(
            title="Language Code (Blank for Automatic)", text=self.values["language"]
        )
        remote.add(self.language)
        self.api_status = Adw.ActionRow(
            title="Credentials",
            subtitle="An API key is required. Keys are stored in your desktop keyring.",
        )
        remote.add(self.api_status)
        self.api_key = Adw.PasswordEntryRow(title="API Key (Blank to Keep the Stored Key)")
        remote.add(self.api_key)
        self.connection_row = Adw.ActionRow(
            title="Connection Test",
            subtitle="Checks the key and selected model without sending audio. Does not save changes.",
        )
        self.connection_button = Gtk.Button(label="Test", valign=Gtk.Align.CENTER)
        self.connection_button.connect("clicked", self.check_connection)
        self.connection_row.add_suffix(self.connection_button)
        remote.add(self.connection_row)
        self.api_key.connect("changed", lambda *_: self.invalidate_connection())
        self.credential_probe = False
        self.cleanup_offer = Adw.ActionRow(
            title="Unused Local Models",
            subtitle="",
            visible=False,
        )
        cleanup_button = Gtk.Button(label="Remove…", valign=Gtk.Align.CENTER)
        cleanup_button.connect("clicked", lambda *_: self.app.remove_models())
        self.cleanup_button = cleanup_button
        self.cleanup_offer.add_suffix(cleanup_button)
        # A storage suggestion is relevant in Remote mode too, without model setup controls.
        self.cleanup_group = Adw.PreferencesGroup()
        self.cleanup_group.add(self.cleanup_offer)
        page.add(self.cleanup_group)
        self.controls["backend"][0].connect(
            "notify::selected", lambda *_: self.backend_visibility()
        )
        self.controls["quantization"][0].connect(
            "notify::selected", lambda *_: self.refresh_backend()
        )
        self.controls["local_model"][0].connect(
            "notify::selected", lambda *_: self.refresh_backend()
        )
        self.controls["device"][0].connect(
            "notify::selected", lambda *_: self.refresh_backend()
        )
        # Backend setup leads; shared microphone and shortcut choices follow it.
        page.remove(inputs)
        page.remove(shortcuts)
        page.add(inputs)
        page.add(shortcuts)
        desktop = Adw.PreferencesGroup(title="Recording and Desktop")
        page.add(desktop)
        threshold = self.controls["silence_threshold"]
        threshold.set_subtitle("Audio level below which a pause counts as silence.")
        desktop.add(threshold)
        self.spin(desktop, "max_seconds", "Maximum Recording (Seconds)", 1, 600)
        self.spin(
            desktop,
            "continuous_minutes",
            "Continuous Session Limit (Minutes)",
            1,
            240,
            "Escape finishes extended dictation. After focus changes, remaining text returns to the original field at completion. Remote mode sends completed phrases to the provider.",
        )
        for key, title, subtitle in [
            (
                "spoken_punctuation",
                "Spoken Punctuation",
                "English commands such as period, dot dot dot, new paragraph and open parenthesis. Supports brackets and braces too. Literal command words also convert; Undo Transcript Changes restores the original.",
            ),
            (
                "auto_paste",
                "Paste Automatically",
                "Only if the original destination stays focused.",
            ),
            (
                "symbolic",
                "Symbolic Tray Icons",
                "Use panel-colored microphones with red recording and amber loading badges.",
            ),
            ("autostart", "Start at Login", "Start quietly when a system tray is available."),
        ]:
            row = Adw.SwitchRow(title=title, subtitle=subtitle, active=self.values[key])
            desktop.add(row)
            self.controls[key] = row
        self.vocabulary = Adw.PreferencesGroup(
            title="Personal Vocabulary",
            description="Whole words and phrases; corrections are reversible on the last transcript.",
        )
        page.add(self.vocabulary)
        self.rules = [list(pair) for pair in self.values["vocabulary"]]
        self.previous_rules = None
        self.rule_rows = []
        actions = Gtk.Box(spacing=8)
        add = Gtk.Button(label="Add Word or Phrase…")
        add.connect("clicked", lambda *_: self.edit_rule())
        actions.append(add)
        self.undo = Gtk.Button(label="Undo Last Edit")
        self.undo.connect("clicked", self.undo_rule)
        actions.append(self.undo)
        self.vocabulary.add(actions)
        bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 480px"))
        bp.add_setter(actions, "orientation", Gtk.Orientation.VERTICAL)
        self.dialog.add_breakpoint(bp)
        self.message = text()
        self.message.add_css_class("error")
        self.vocabulary.add(self.message)
        self.render_rules()
        self.backend_visibility()
        self.dialog.connect("close-request", self.closed)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.key_pressed)
        self.dialog.add_controller(keys)
        self.check_compute()

    @staticmethod
    def format_silence_stop(row):
        if row.get_value() == 0:
            row.set_text("Off")
            return True
        return False

    def check_compute(self):
        def check():
            try:
                capability = compute_capabilities(self.compute_cancel)
            except InterruptedError:
                return

            def finish():
                if self.closed_flag:
                    return
                self.compute_capability = capability
                self.refresh_backend()

            self.app.dispatch(finish)

        threading.Thread(target=check, daemon=True).start()

    def show_model_guide(self):
        from .model_guide import ModelGuide

        if self.model_guide is None:
            self.model_guide = ModelGuide()
        self.model_guide.present(self.dialog)

    def model_constraints(self):
        spec = LOCAL_MODELS[self.selected_model()]
        self.model_description.set_subtitle(spec["description"])
        precision, values = self.controls["quantization"]
        if self.selected_precision() not in spec["sizes"]:
            precision.set_selected(values.index(next(iter(spec["sizes"]))))
        precision.set_sensitive(len(spec["sizes"]) > 1)
        device, _ = self.controls["device"]
        cpu_only = spec.get("engine") == "moonshine"
        capability = self.compute_capability
        device.set_sensitive(not cpu_only and bool(capability and capability["cuda"]))
        if cpu_only:
            device.set_selected(0)
            self.compute_status.set_subtitle(
                "Moonshine Tiny uses CPU. An NVIDIA card is not needed."
            )
        elif capability is not None:
            if capability["cuda"]:
                self.compute_status.set_subtitle("CUDA is available in this installation.")
            else:
                device.set_selected(0)
                self.compute_status.set_subtitle(
                    capability["reason"]
                    + " CPU is available. CUDA requires a compatible NVIDIA GPU, driver and GPU-enabled ONNX Runtime."
                )
        else:
            self.compute_status.set_subtitle("Checking this installation…")

    def combo(self, group, key, title, choices):
        values, labels = zip(*choices)
        row = Adw.ComboRow(
            title=title,
            model=Gtk.StringList.new(labels),
            use_subtitle=True,
            selected=values.index(self.values[key]),
        )
        group.add(row)
        self.controls[key] = (row, values)

    def spin(self, group, key, title, low, high, subtitle=""):
        row = Adw.SpinRow.new_with_range(low, high, 1)
        row.set_title(title)
        row.set_subtitle(subtitle)
        row.set_value(self.values[key])
        group.add(row)
        self.controls[key] = row

    def selected_precision(self):
        row, values = self.controls["quantization"]
        return values[row.get_selected()]

    def selected_model(self):
        row, values = self.controls["local_model"]
        return values[row.get_selected()]

    def backend_visibility(self):
        cloud = self.controls["backend"][0].get_selected() == 1
        self.local_group.set_visible(not cloud)
        self.remote_group.set_visible(cloud)
        self.invalidate_connection()
        self.refresh_backend()
        if cloud and not self.credential_probe:
            self.credential_probe = True
            self.api_status.set_subtitle("Checking the desktop keyring…")

            def probe():
                try:
                    available = bool(read_key())
                    message = (
                        "A key is available. Leave the field blank to keep it, or enter a replacement."
                        if available
                        else "API key required. Enter a key below, then Save."
                    )
                    if os.environ.get("OPENAI_API_KEY"):
                        message = "Using OPENAI_API_KEY from the environment. It overrides the stored key and any key entered below."
                except Exception:
                    message = "The desktop keyring is unavailable. Unlock it before saving an API key."
                self.app.dispatch(
                    lambda: (
                        self.api_status.set_subtitle(message) if not self.closed_flag else None
                    )
                )

            threading.Thread(target=probe, daemon=True).start()

    def refresh_backend(self):
        self.model_constraints()
        c = self.app.session
        local = self.controls["backend"][0].get_selected() == 0
        precision = self.selected_precision()
        model = self.selected_model()
        device_row, device_values = self.controls["device"]
        matches = (
            local
            and c.preferences.backend == "local"
            and model == c.preferences.local_model
            and precision == c.preferences.quantization
            and device_values[device_row.get_selected()] == c.preferences.device
        )
        cached = downloaded(precision, model)
        self.voice_banner_group.set_visible(local)
        self.voice_group.set_visible(local)
        self.controls["hands_free"].set_sensitive(
            local and (cached or self.controls["hands_free"].get_active())
        )
        self.refresh_voice_status(cached)
        self.setup_group.set_visible(local and not cached)
        self.setup_row.set_title(f"{LOCAL_MODELS[model]['name']} isn’t downloaded")
        self.setup_row.set_subtitle(
            f"Download approximately {LOCAL_MODELS[model]['sizes'][precision]}. This is a one-time download; your audio stays on this computer."
        )
        self.setup_download.set_sensitive(not c.busy)
        self.download_button.set_sensitive(local and not c.busy and not cached)
        self.download_row.set_subtitle(
            "Installed · Download approximately " + LOCAL_MODELS[model]["sizes"][precision]
            if cached
            else "Not installed · Download approximately "
            + LOCAL_MODELS[model]["sizes"][precision]
        )
        self.selected_remove_button.set_sensitive(
            local and not c.busy and any(downloaded(p, model) for p in ("int8", "float32"))
        )
        self.selected_remove_row.set_subtitle(
            f"Only {LOCAL_MODELS[model]['name']} downloads and backups. Other models remain installed."
        )
        self.model_button.set_label("Unload" if c.recognizer.loaded else "Load")
        self.model_button.set_sensitive(
            matches
            and not c.busy
            and (cached or c.recognizer.loaded)
            and not (c.recognizer.loaded and c.preferences.hands_free)
        )
        self.model_row.set_subtitle(
            (
                "Kept loaded for hands-free commands."
                if c.recognizer.loaded and c.preferences.hands_free
                else "Loaded and ready."
                if c.recognizer.loaded
                else "Not loaded."
            )
            if matches
            else "Save these choices to use this model for dictation."
        )
        self.repair.set_sensitive(local and not c.busy and cached)
        self.connection_button.set_sensitive(not self.connection_busy and not c.busy)
        if self.storage_phase != (c.phase, c.operation) and not self.storage_busy:
            self.storage_phase = (c.phase, c.operation)
            self.scan_storage()
        self.show_storage()

    def refresh_voice_status(self, cached):
        c, listener = self.app.session, self.app.handsfree
        phrase = c.preferences.wake_phrase
        if not cached:
            status = "Download the selected local model before enabling hands-free commands."
        elif not listener or not listener.enabled:
            status = "Listening is off. Enable hands-free commands and Save to start."
        elif listener.problem:
            status = listener.problem
        elif c.busy:
            status = "Listening is paused while DictaDesk finishes the current operation."
        else:
            status = {
                "wake": f"Listening for “{phrase}”. Audio stays on this computer.",
                "processing": f"Checking speech for “{phrase}”…",
                "command": "Wake phrase heard. Listening for a command…",
                "loading": "Loading the local speech model…",
                "paused": "Voice commands are paused. Resume them from the tray.",
            }.get(listener.state, f"Starting local listening for “{phrase}”…")
        draft = self.wake_phrase.get_text().strip()
        if draft != phrase:
            status += f" Save to change the active wake phrase from “{phrase}” to “{draft}”."
        self.voice_status.set_subtitle(status)

    def scan_storage(self):
        self.storage_busy = True

        def scan():
            try:
                info = cache_info()
            except (OSError, ValueError):
                info = None

            def finish():
                self.storage_busy = False
                if not self.closed_flag:
                    self.storage_info = info
                    self.show_storage()

            self.app.dispatch(finish)

        threading.Thread(target=scan, daemon=True).start()

    def show_storage(self):
        info = self.storage_info
        exists = bool(info and info["exists"])
        self.remove_button.set_sensitive(exists and not self.app.session.busy)
        self.cleanup_button.set_sensitive(exists and not self.app.session.busy)
        self.storage_row.set_subtitle(
            f"{format_size(info['bytes'])} on disk, including backups."
            if exists
            else (
                "No downloaded models."
                if info
                else (
                    "Checking model storage…"
                    if self.storage_busy
                    else "Could not read model storage."
                )
            )
        )
        days = info["unused_days"] if info else None
        stale = exists and days is not None and days >= UNUSED_DAYS
        self.cleanup_offer.set_visible(stale)
        self.cleanup_group.set_visible(stale)
        if stale:
            self.cleanup_offer.set_subtitle(
                f"No local model use for {days} days. Remove {format_size(info['bytes'])} of downloads and backups? Your settings and transcripts will stay."
            )

    def invalidate_connection(self):
        self.connection_generation += 1
        self.connection_row.set_subtitle(
            "Checks the key and selected model without sending audio. Does not save changes."
        )

    def check_connection(self, *_):
        if self.connection_busy:
            return
        self.connection_busy = True
        self.connection_generation += 1
        generation = self.connection_generation
        key = self.api_key.get_text().strip()
        model = self.values["cloud_model"]
        self.connection_button.set_sensitive(False)
        self.connection_row.set_subtitle("Connecting…")

        def check():
            try:
                # Match runtime precedence: an environment key overrides the keyring.
                effective_key = os.environ.get("OPENAI_API_KEY") or key or read_key()
                message = test_connection(effective_key, model)
            except Exception:
                message = "The desktop keyring is unavailable. Enter a key to test or unlock the keyring."

            def finish():
                self.connection_busy = False
                if not self.closed_flag:
                    self.connection_button.set_sensitive(not self.app.session.busy)
                    if generation == self.connection_generation:
                        self.connection_row.set_subtitle(message)

            self.app.dispatch(finish)

        threading.Thread(target=check, daemon=True).start()

    def download_model(self, *_):
        self.app.session.download_model(self.selected_precision(), model=self.selected_model())

    def populate_devices(self):
        self.device_snapshot = tuple(self.app.session.devices)
        selected = (self.values["microphone"], self.values["host_api"])
        if self.device_values:
            selected = self.device_values[self.input.get_selected()]
        devices = [("", "")] + list(
            dict.fromkeys((name, host) for _, name, host in self.app.session.devices)
        )
        labels = ["System Default"] + [f"{name} · {host}" for name, host in devices[1:]]
        if selected not in devices:
            devices.append(selected)
            labels.append(selected[0] + " — unavailable")
        self.device_values = devices
        self.input.set_model(Gtk.StringList.new(labels))
        self.input.set_selected(devices.index(selected))

    def capture_shortcut(self, key):
        dialog = Adw.AlertDialog(
            heading="Press a Shortcut",
            body="Hold Super, Ctrl or Alt and press a letter or number.",
        )
        dialog.add_response("cancel", "Cancel")
        event = Gtk.EventControllerKey()

        def capture(controller, keyval, keycode, state):
            if keyval == Gdk.KEY_Escape:
                dialog.close()
                return True
            letter = Gdk.keyval_to_unicode(keyval)
            names = [
                (Gdk.ModifierType.SUPER_MASK, "Super"),
                (Gdk.ModifierType.CONTROL_MASK, "Control"),
                (Gdk.ModifierType.ALT_MASK, "Alt"),
                (Gdk.ModifierType.SHIFT_MASK, "Shift"),
            ]
            chord = "".join(f"<{name}>" for mask, name in names if state & mask) + (
                chr(letter).lower() if letter else ""
            )
            try:
                parsed = Chord.parse(chord)
            except ValueError:
                return True
            self.values[key] = chord
            self.shortcut_rows[key].set_subtitle(str(parsed))
            self.update_usage()
            dialog.close()
            return True

        event.connect("key-pressed", capture)
        dialog.add_controller(event)
        dialog.present(self.dialog)

    def model_action(self, *_):
        if self.app.session.recognizer.loaded:
            self.app.session.unload()
        else:
            self.app.session.load()

    def render_rules(self):
        for row in self.rule_rows:
            self.vocabulary.remove(row)
        self.rule_rows = []
        for index, (source, replacement) in enumerate(self.rules):
            row = Adw.ActionRow(title=source, subtitle=replacement, use_markup=False)
            for icon, label, callback in [
                (
                    "document-edit-symbolic",
                    "Edit Vocabulary Entry",
                    lambda _, i=index: self.edit_rule(i),
                ),
                (
                    "user-trash-symbolic",
                    "Remove Vocabulary Entry",
                    lambda _, i=index: self.remove_rule(i),
                ),
            ]:
                button = Gtk.Button(icon_name=icon, tooltip_text=label, valign=Gtk.Align.CENTER)
                button.connect("clicked", callback)
                row.add_suffix(button)
            self.vocabulary.add(row)
            self.rule_rows.append(row)
        self.undo.set_sensitive(self.previous_rules is not None)

    def replace_rules(self, rules):
        candidate = Preferences(**{**self.values, "vocabulary": rules})
        candidate.validate()
        self.previous_rules, self.rules = self.rules, rules
        self.render_rules()

    def remove_rule(self, index):
        self.replace_rules([pair for i, pair in enumerate(self.rules) if i != index])

    def undo_rule(self, *_):
        if self.previous_rules is not None:
            self.rules, self.previous_rules = self.previous_rules, None
            self.render_rules()

    def edit_rule(self, index=None):
        dialog = Adw.AlertDialog(heading="Vocabulary Entry")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        source = Adw.EntryRow(title="Recognized As")
        replacement = Adw.EntryRow(title="Replace With")
        group = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        group.add_css_class("boxed-list")
        group.append(source)
        group.append(replacement)
        box.append(group)
        message = text()
        box.append(message)
        if index is not None:
            source.set_text(self.rules[index][0])
            replacement.set_text(self.rules[index][1])
        dialog.set_extra_child(box)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("apply", "Apply")
        dialog.set_response_appearance("apply", Adw.ResponseAppearance.SUGGESTED)

        def apply(_, response):
            if response != "apply":
                return
            pair = [source.get_text().strip(), replacement.get_text().strip()]
            rules = [list(pair) for pair in self.rules]
            if index is None:
                rules.append(pair)
            else:
                rules[index] = pair
            try:
                self.replace_rules(rules)
            except ValueError as exc:
                self.message.set_text(str(exc))

        dialog.connect("response", apply)
        dialog.present(self.dialog)

    def save_clicked(self, *_):
        self.validation_problem = ""
        try:
            data = dict(self.values)
            for key, control in self.controls.items():
                if isinstance(control, tuple):
                    row, choices = control
                    data[key] = choices[row.get_selected()]
                elif isinstance(control, Adw.SwitchRow):
                    data[key] = control.get_active()
                else:
                    data[key] = (
                        control.get_value()
                        if key
                        in ("pause_seconds", "sentence_pause_seconds", "silence_threshold")
                        else int(control.get_value())
                    )
            data["microphone"], data["host_api"] = self.device_values[self.input.get_selected()]
            data["language"] = self.language.get_text().strip()
            data["wake_phrase"] = self.wake_phrase.get_text().strip()
            if (
                data["backend"] == "local"
                and data["hands_free"]
                and not downloaded(data["quantization"], data["local_model"])
            ):
                raise ValueError(
                    "Download the selected local model before enabling hands-free commands."
                )
            data["vocabulary"] = self.rules
            prefs = Preferences(**data).validate()
            chords = [
                Chord.parse(data[key])
                for key in (
                    "shortcut",
                    "continuous_shortcut",
                    "cancel_shortcut",
                    "paste_shortcut",
                )
            ]
            if len({(c.key, c.mask) for c in chords}) != 4:
                raise ValueError("Each action needs a different shortcut.")
            self.app.save_preferences(prefs, self.api_key.get_text().strip(), self.dialog.close)
        except Exception as exc:
            self.validation_problem = str(exc)
            self.refresh()

    def refresh(self):
        if tuple(self.app.session.devices) != self.device_snapshot:
            self.populate_devices()
        busy = self.app.session.busy
        self.save.set_sensitive(not busy)
        self.refresh_backend()
        c = self.app.session
        self.message.set_text(c.notice if c.phase == "idle" and not c.problem else "")
        shortcut_problem = self.app.shortcut_problem or self.app.escape_problem
        self.shortcut_status.set_subtitle(shortcut_problem)
        self.shortcut_status.set_visible(bool(shortcut_problem))
        microphone_problem = (
            "" if c.microphone_problem == "Checking microphone…" else c.microphone_problem
        )
        self.microphone_status.set_subtitle(
            microphone_problem + " Refresh the list, choose an input, then test it."
        )
        self.microphone_status.set_visible(bool(microphone_problem))
        if self.validation_problem:
            title, detail = "Check your changes", self.validation_problem
        elif c.store.problem:
            title, detail = (
                "Settings need recovery",
                c.store.problem + " Use Back Up Settings and Start Fresh below.",
            )
        elif self.app.accessibility_problem:
            title, detail = "Automatic insertion unavailable", self.app.accessibility_problem
        elif c.problem:
            _, category, detail = c.problem
            title = {
                "loading": "Model needs attention",
                "saving": "Settings need attention",
                "recording": "Dictation stopped",
                "delivery": "Text delivery needs attention",
            }.get(category, "Operation needs attention")
            if category == "loading":
                detail += " Check the selected local model below; use Download or Repair Model if needed."
        elif c.preferences.backend == "openai" and not c.credential_available:
            title, detail = (
                "Remote transcription needs setup",
                "Add your API key under Remote Transcription, test the connection, then Save.",
            )
        else:
            title, detail = "", ""
        self.attention.set_title(title)
        self.attention.set_subtitle(detail)
        self.attention_group.set_visible(bool(detail))
        self.recovery.set_visible(bool(c.store.problem))

    def key_pressed(self, controller, keyval, keycode, state):
        if keyval == Gdk.KEY_Escape and self.dialog.get_visible_dialog() is None:
            self.dialog.close()
            return True
        return False

    def closed(self, *_):
        self.closed_flag = True
        self.compute_cancel.set()
        self.api_key.set_text("")
        self.app.preferences_dialog = None
        self.app.bind_shortcuts()

    def present(self):
        if self.app.keyboard:
            self.app.keyboard.clear()
        self.refresh()
        self.dialog.present()
        self.app.dispatch(self.offer_model_download)

    def offer_model_download(self):
        if (
            self.closed_flag
            or self.app.shutting_down
            or self.model_prompt
            or self.app.session.busy
            or self.app.session.store.problem
        ):
            return
        if self.controls["backend"][0].get_selected() != 0 or self.dialog.get_visible_dialog():
            return
        model, precision = self.selected_model(), self.selected_precision()
        if downloaded(precision, model) or (model, precision) in self.app.model_prompts_shown:
            return
        self.app.model_prompts_shown.add((model, precision))
        spec = LOCAL_MODELS[model]
        dialog = Adw.AlertDialog(
            heading="Download a Speech Model?",
            body=f"DictaDesk needs a speech model before it can transcribe your voice locally. Download {spec['name']} (approximately {spec['sizes'][precision]}) now?\n\nDownload once; your audio stays on this computer. You can choose a different model in Preferences.",
        )
        dialog.add_response("later", "Not Now")
        dialog.add_response("download", "Download Model")
        dialog.set_response_appearance("download", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("download")
        dialog.set_close_response("later")
        self.model_prompt = dialog

        def response(_, choice):
            self.model_prompt = None
            if choice == "download" and not self.closed_flag:
                self.app.session.download_model(precision, model=model)

        dialog.connect("response", response)
        dialog.present(self.dialog)

    def sample_settings(self):
        data = dict(self.values)
        for key in ("backend", "local_model", "quantization", "device"):
            row, choices = self.controls[key]
            data[key] = choices[row.get_selected()]
        data["microphone"], data["host_api"] = self.device_values[self.input.get_selected()]
        data["language"] = self.language.get_text().strip()
        data["spoken_punctuation"] = self.controls["spoken_punctuation"].get_active()
        data["vocabulary"] = self.rules
        key = os.environ.get("OPENAI_API_KEY") or self.api_key.get_text().strip()
        return Preferences(**data).validate(), key

    def update_usage(self):
        from html import escape

        shortcut = escape(str(Chord.parse(self.values["shortcut"])))
        self.usage.set_markup(
            "1. Click in any text field where you want to dictate.\n"
            f"2. Press <b>{shortcut}</b> to start recording.\n"
            "3. Speak normally.\n"
            f"4. Press <b>{shortcut}</b> again or <b>Escape</b> to finish.\n\n"
            "DictaDesk runs in the system tray. You don’t need to open a window to use it."
        )
