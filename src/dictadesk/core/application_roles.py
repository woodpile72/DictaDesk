# SPDX-License-Identifier: GPL-3.0-only
"""Common application roles, shared by default resolution and command help."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ApplicationRole:
    ident: str
    name: str
    aliases: tuple
    content_types: tuple = ()
    setting: str = ""

    @property
    def spoken_names(self):
        return tuple(
            prefix + alias
            for prefix in ("", "the ", "my ", "default ", "the default ", "my default ")
            for alias in self.aliases
        )


# These MIME associations mirror Cinnamon's Preferred Applications selectors.
# Additional types are fallbacks for desktops using other common formats.
APPLICATION_ROLES = (
    ApplicationRole(
        "browser",
        "web browser",
        ("browser", "web browser", "internet browser"),
        ("x-scheme-handler/http", "x-scheme-handler/https", "text/html"),
    ),
    ApplicationRole(
        "files",
        "file manager",
        ("file manager", "files", "file browser", "home folder"),
        ("inode/directory",),
    ),
    ApplicationRole(
        "mail",
        "mail application",
        ("email", "mail", "email client", "mail client", "e mail"),
        ("x-scheme-handler/mailto",),
    ),
    ApplicationRole(
        "terminal", "terminal", ("terminal", "terminal emulator", "console"), setting="terminal"
    ),
    ApplicationRole("calculator", "calculator", ("calculator",), setting="calculator"),
    ApplicationRole(
        "text", "text editor", ("text editor", "editor", "plain text editor"), ("text/plain",)
    ),
    ApplicationRole(
        "music",
        "music player",
        ("music player", "music", "audio player"),
        ("audio/x-vorbis+ogg", "audio/mpeg", "audio/ogg"),
    ),
    ApplicationRole(
        "video",
        "video player",
        ("video player", "video", "videos", "movie player"),
        ("video/x-ogm+ogg", "video/mp4", "video/ogg"),
    ),
    ApplicationRole(
        "images",
        "image viewer",
        ("image viewer", "photos", "photo viewer", "pictures"),
        ("image/jpeg", "image/png"),
    ),
    ApplicationRole(
        "pdf",
        "PDF reader",
        ("pdf reader", "pdf viewer", "document viewer"),
        ("application/pdf",),
    ),
    ApplicationRole(
        "word",
        "word processor",
        ("word processor", "word processing"),
        (
            "application/msword",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
    ),
    ApplicationRole(
        "spreadsheet",
        "spreadsheet application",
        ("spreadsheet", "spreadsheets"),
        (
            "application/msexcel",
            "application/vnd.ms-excel",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ),
    ),
    ApplicationRole(
        "code",
        "source code editor",
        ("code editor", "source code editor", "source editor"),
        ("text/x-python",),
    ),
)

ROLE_NAMES = {
    tuple(name.split()): role for role in APPLICATION_ROLES for name in role.spoken_names
}


def application_role(query):
    return ROLE_NAMES.get(query)
