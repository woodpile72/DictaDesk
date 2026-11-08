# SPDX-License-Identifier: GPL-3.0-only
from .gtk import Gtk, Pango


def experimental_banner():
    banner = Gtk.Label(label="Experimental", wrap=True, hexpand=True)
    banner.add_css_class("experimental-banner")
    css = Gtk.CssProvider()
    css.load_from_data(
        b".experimental-banner { background-color: #b71c1c; color: #ffffff; "
        b"font-weight: 700; padding: 10px 12px; border-radius: 8px; }"
    )
    banner.get_style_context().add_provider(css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    return banner


def text(value="", **kwargs):
    return Gtk.Label(
        label=value, xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, **kwargs
    )
