# SPDX-License-Identifier: GPL-3.0-only
from .gtk import Gtk, Pango


def text(value="", **kwargs):
    return Gtk.Label(
        label=value, xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, **kwargs
    )


def icon_button(icon, name, action):
    button = Gtk.Button(
        icon_name=icon, tooltip_text=name, action_name=action, valign=Gtk.Align.CENTER
    )
    button.update_property([Gtk.AccessibleProperty.LABEL], [name])
    return button
