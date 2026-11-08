# SPDX-License-Identifier: GPL-3.0-only
"""External GTK3 accessibility provider for the private-desktop tests."""

import sys
import threading

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib  # noqa: E402

window = Gtk.Window(title="DictaDesk origin fixture")
window.set_default_size(600, 300)
box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
window.add(box)
first, second = Gtk.Entry(), Gtk.Entry()
first.set_text("before selected after")
first.get_accessible().set_name("Original field")
second.set_text("Unrelated field")
second.get_accessible().set_name("Other field")
box.pack_start(first, False, False, 0)
box.pack_start(second, False, False, 0)
button = Gtk.Button(label="No editable focus")
box.pack_start(button, False, False, 0)
other = Gtk.Window(title="DictaDesk other fixture")
other.set_default_size(500, 250)
other.move(650, 350)
third = Gtk.Entry()
third.set_text("Other application text")
third.get_accessible().set_name("Other window field")
other.add(third)
window.show_all()
other.show_all()


def apply(command):
    if command == "remove":
        box.remove(first)
    elif command == "close":
        window.destroy()
    elif command == "button":
        button.grab_focus()
    elif command == "quit":
        Gtk.main_quit()
    return False


def commands():
    for line in sys.stdin:
        GLib.idle_add(apply, line.strip())


threading.Thread(target=commands, daemon=True).start()
Gtk.main()
