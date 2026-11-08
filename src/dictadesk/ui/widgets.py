# SPDX-License-Identifier: GPL-3.0-only
from .gtk import Gtk, Pango


def text(value="", **kwargs):
    return Gtk.Label(
        label=value, xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, **kwargs
    )
