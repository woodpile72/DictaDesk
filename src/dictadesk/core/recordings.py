# SPDX-License-Identifier: GPL-3.0-only
"""Private recording workspaces with process-held leases for crash cleanup."""

from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import re
import shutil
import stat
import uuid


def private_directory(path):
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise ValueError("The temporary recording directory must be private and owned by you.")


@contextmanager
def locked_file(path, *, nonblocking=False, create=True):
    flags = os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC
    if create:
        flags |= os.O_CREAT
    fd = os.open(path, flags, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
            raise ValueError("Invalid temporary recording lock.")
        fcntl.flock(fd, fcntl.LOCK_EX | (fcntl.LOCK_NB if nonblocking else 0))
        yield
    finally:
        os.close(fd)


@contextmanager
def workspace(root):
    root = Path(root)
    private_directory(root)
    # Creation and cleanup use the same guard so an unfinished workspace is never
    # mistaken for an abandoned one. The lease lasts through transcription.
    with locked_file(root / ".cleanup.lock"):
        path = root / ("session-" + uuid.uuid4().hex)
        private_directory(path)
        lease = locked_file(path / ".active")
        lease.__enter__()
    try:
        yield path
    finally:
        try:
            with locked_file(root / ".cleanup.lock"):
                shutil.rmtree(path)
        finally:
            lease.__exit__(None, None, None)


def cleanup_abandoned(root):
    root = Path(root)
    if not root.exists() and not root.is_symlink():
        return 0
    private_directory(root)
    removed = 0
    with locked_file(root / ".cleanup.lock"):
        for path in root.iterdir():
            if not re.fullmatch(r"session-[0-9a-f]{32}", path.name):
                continue
            info = path.lstat()
            if (
                not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o700
            ):
                continue
            try:
                with locked_file(path / ".active", nonblocking=True, create=False):
                    shutil.rmtree(path)
                    removed += 1
            except (BlockingIOError, FileNotFoundError):
                continue
    return removed
