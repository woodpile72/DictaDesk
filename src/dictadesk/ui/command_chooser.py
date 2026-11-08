# SPDX-License-Identifier: GPL-3.0-only
"""Desktop-covering command chooser. This is attention UI, not a screen lock."""

import os

from .gtk import Adw, Gdk, Gtk


def primary_monitor(monitors, connector=None):
    """Prefer the desktop's primary output, with a deterministic fallback."""
    for monitor in monitors:
        if connector and monitor.get_connector() == connector:
            return monitor
    for monitor in monitors:
        rect = monitor.get_geometry()
        if rect.x <= 0 < rect.x + rect.width and rect.y <= 0 < rect.y + rect.height:
            return monitor
    return monitors[0]


def primary_connector():
    if os.environ.get("XDG_SESSION_TYPE") != "x11":
        return None
    from Xlib import display

    connection = None
    try:
        connection = display.Display()
        output = connection.screen().root.xrandr_get_output_primary().output
        if output:
            name = connection.xrandr_get_output_info(output, 0).name
            return name.decode() if isinstance(name, bytes) else name
    except Exception:
        # A missing RandR primary hint must never prevent dismissal/selection.
        return None
    finally:
        if connection:
            connection.close()
    return None


class CommandChooser(Adw.Window):
    def __init__(self, application, title, context):
        super().__init__(application=application, title=title, modal=True, decorated=False)
        self.covers = []
        self.cleaned = False
        self.display = self.get_display()
        monitors = list(self.display.get_monitors())
        self.primary = primary_monitor(monitors, primary_connector()) if monitors else None
        self.provider = Gtk.CssProvider()
        self.provider.load_from_data(b"""
            window.dictadesk-command-cover { background: rgba(0, 0, 0, 0.76); }
            .dictadesk-command-card { background: @window_bg_color; color: @window_fg_color;
                border-radius: 18px; padding: 24px; font-size: 1.4em; }
            .dictadesk-command-card .command-title { font-size: 1.45em; font-weight: bold; }
            .dictadesk-command-card row { padding: 6px; }
            .dictadesk-command-card .command-context { font-size: 0.85em; }
            .dictadesk-command-card.compact { padding: 12px; }
            .dictadesk-command-card.compact row { padding: 0; }
        """)
        Gtk.StyleContext.add_provider_for_display(
            self.display, self.provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        self.add_css_class("dictadesk-command-cover")
        self.card = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=12, valign=Gtk.Align.CENTER
        )
        self.card.add_css_class("dictadesk-command-card")
        if self.primary and self.primary.get_geometry().height < 750:
            self.card.add_css_class("compact")
            self.card.set_spacing(8)
        heading = Gtk.Box(spacing=24)
        label = Gtk.Label(label=title, xalign=0, wrap=True, hexpand=True)
        label.add_css_class("command-title")
        heading.append(label)
        self.cancel = Gtk.Button(label="Cancel", valign=Gtk.Align.CENTER)
        self.cancel.set_tooltip_text("Cancel (Escape)")
        self.cancel.connect("clicked", lambda *_: self.close())
        heading.append(self.cancel)
        self.card.append(heading)
        self.context = Gtk.Label(label=context, wrap=True, xalign=0)
        self.context.add_css_class("command-context")
        self.context.set_lines(2)
        self.card.append(self.context)
        self.hint = Gtk.Label(wrap=True, xalign=0)
        self.card.append(self.hint)
        self.listing = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.listing.add_css_class("boxed-list")
        self.card.append(self.listing)
        clamp = Adw.Clamp(
            maximum_size=840,
            tightening_threshold=840,
            child=self.card,
            margin_top=24,
            margin_bottom=24,
            margin_start=24,
            margin_end=24,
            vexpand=True,
        )
        # Only exceptionally small displays/very large fonts need scrolling.
        self.scroll = Gtk.ScrolledWindow(child=clamp, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.set_content(self.scroll)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.key_pressed)
        self.add_controller(keys)
        self.connect("close-request", self.cleanup)
        self.connect("unrealize", self.cleanup)
        for monitor in monitors:
            if monitor == self.primary:
                continue
            cover = Gtk.Window(
                application=application, decorated=False, title="DictaDesk Voice Commands"
            )
            cover.add_css_class("dictadesk-command-cover")
            cover.set_focusable(False)
            cover.set_child(Gtk.Box())
            cover.fullscreen_on_monitor(monitor)
            cover.connect("close-request", lambda *_: self.close() or False)
            self.covers.append(cover)
        if self.primary:
            rect = self.primary.get_geometry()
            self.set_default_size(rect.width, rect.height)
            self.fullscreen_on_monitor(self.primary)

    def present(self):
        for cover in self.covers:
            cover.set_visible(True)
        super().present()
        self.cancel.grab_focus()

    def key_pressed(self, _controller, key, _code, _modifiers):
        if key == Gdk.KEY_Escape:
            self.close()
            return True
        return False

    def cleanup(self, *_):
        if not self.cleaned:
            self.cleaned = True
            for cover in self.covers:
                cover.destroy()
            self.covers.clear()
            Gtk.StyleContext.remove_provider_for_display(self.display, self.provider)
        return False
