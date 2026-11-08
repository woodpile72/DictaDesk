# SPDX-License-Identifier: GPL-3.0-only
"""Resolve trusted desktop defaults without running settings or spoken shell text."""

import os
from pathlib import Path

from ..core.voice_commands import launchable_application
from ..ui.gtk import Gio, GLib

TERMINAL_FALLBACKS = (
    "org.gnome.Terminal.desktop",
    "gnome-terminal.desktop",
    "xfce4-terminal.desktop",
    "org.kde.konsole.desktop",
    "mate-terminal.desktop",
)


def configured_application(kind):
    """Read the active desktop's terminal/calculator preference, if supported."""
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").casefold().split(":")
    schemas = [
        f"org.cinnamon.desktop.default-applications.{kind}",
        f"org.gnome.desktop.default-applications.{kind}",
        f"org.mate.applications-{kind}",
    ]
    if any("cinnamon" in name for name in desktop):
        schemas = schemas[:1]
    elif any("gnome" in name for name in desktop):
        schemas = schemas[1:2]
    elif any("mate" == name for name in desktop):
        schemas = schemas[2:]
    elif any(desktop):
        # Installed schemas from a different desktop are not this desktop's
        # preference. Leave unsupported terminals to the existing fallback.
        return None
    source = Gio.SettingsSchemaSource.get_default()
    if source:
        for name in schemas:
            schema = source.lookup(name, True)
            if schema and schema.has_key("exec"):
                value = Gio.Settings.new_full(schema, None, None).get_string("exec").strip()
                if value:
                    return value
    return None


def command_arguments(command):
    """Compare configured commands to installed entries; never execute the string."""
    try:
        _, arguments = GLib.shell_parse_argv(command)
    except GLib.Error:
        return None
    if not arguments:
        return None
    return (Path(arguments[0]).name,) + tuple(
        arg for arg in arguments[1:] if arg not in ("%f", "%F", "%u", "%U", "%i", "%c", "%k")
    )


def preferred_applications(role, applications, *, default_lookup=None, setting_lookup=None):
    default_lookup = default_lookup or Gio.AppInfo.get_default_for_type
    setting_lookup = setting_lookup or configured_application
    installed = {app.get_id(): app for app in applications if app.get_id()}
    if role.content_types:
        for content_type in role.content_types:
            default = default_lookup(content_type, False)
            if default and default.get_id() in installed:
                # A configured MIME handler can legitimately be absent from
                # menus; honor the explicit desktop association, but not deleted entries.
                if not getattr(default, "get_is_hidden", lambda: False)():
                    return [default]
        raise ValueError(
            f"No default {role.name} is configured. Choose one in Preferred Applications."
        )
    configured = setting_lookup(role.setting)
    if configured:
        desired = command_arguments(configured)
        matches = [
            app
            for app in installed.values()
            if launchable_application(app)
            and desired
            and command_arguments(app.get_commandline() or "") == desired
        ]
        if matches:
            return sorted(matches, key=lambda app: app.get_display_name().casefold())
        raise ValueError(
            f"The configured {role.name} has no matching launchable desktop entry. Check Preferred Applications."
        )
    # Preserve the existing terminal fallback on desktops without this setting.
    if role.setting == "terminal":
        for ident in TERMINAL_FALLBACKS:
            if ident in installed and launchable_application(installed[ident]):
                return [installed[ident]]
    raise ValueError(
        f"No default {role.name} is configured. Choose one in Preferred Applications."
    )
