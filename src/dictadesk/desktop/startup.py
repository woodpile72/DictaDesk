# SPDX-License-Identifier: GPL-3.0-only
"""Explicit per-user startup registration with this application's own identity."""

import os
import shutil
from pathlib import Path

from ..core.settings import private_write


def set_enabled(enabled):
    target = (
        Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        / "autostart/io.github.woodpile72.DictaDesk.desktop"
    )
    if not enabled:
        target.unlink(missing_ok=True)
        return
    executable = shutil.which("dictadesk")
    if not executable or any(c in executable for c in ("\n", '"', "%", "`", "$", "\\")):
        raise RuntimeError("Install DictaDesk before enabling Start at Login.")
    private_write(
        target,
        f'[Desktop Entry]\nType=Application\nName=DictaDesk\nExec="{executable}" --background\nIcon=io.github.woodpile72.DictaDesk\nTerminal=false\n'.encode(),
    )
