# SPDX-License-Identifier: GPL-3.0-only
"""Open a search URL through the default browser without shell interpretation."""

from ..core.search import search_url
from ..ui.gtk import Gio, GLib


def launch(query, engine, valid, done):
    if not valid():
        return
    try:
        url = search_url(engine, query)
    except ValueError as exc:
        done(str(exc))
        return

    def finished(_source, result):
        try:
            success = Gio.AppInfo.launch_default_for_uri_finish(result)
            message = (
                "" if success else "The search could not be opened. Check your default browser."
            )
        except GLib.Error:
            message = "The search could not be opened. Check your default browser."
        if valid():
            done(message)

    try:
        Gio.AppInfo.launch_default_for_uri_async(url, None, None, finished)
    except GLib.Error:
        if valid():
            done("The search could not be opened. Check your default browser.")
