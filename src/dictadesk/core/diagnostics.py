# SPDX-License-Identifier: GPL-3.0-only
"""Bounded private metadata logs. Never pass audio, transcripts or credentials here."""

import json
import logging
from logging.handlers import RotatingFileHandler
import os
import threading

from .settings import directory

_guard = threading.Lock()
_logger = None
_path = None
WORKER_PREFIX = "DICTADESK_DIAGNOSTIC "


class PrivateHandler(RotatingFileHandler):
    def _open(self):
        fd = os.open(
            self.baseFilename, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600
        )
        os.fchmod(fd, 0o600)
        return os.fdopen(fd, "a", encoding="utf-8")


def event(name, **metadata):
    global _logger, _path
    try:
        # Worker protocol stdout is reserved for replies. Only our structured
        # metadata is forwarded; third-party stderr is never saved verbatim.
        if os.environ.get("DICTADESK_WORKER_DIAGNOSTICS") == "1":
            import sys

            print(
                WORKER_PREFIX + json.dumps({"event": name, **metadata}),
                file=sys.stderr,
                flush=True,
            )
            return
        path = directory("STATE") / "diagnostics.log"
        with _guard:
            if _logger is None or path != _path:
                path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                fd = os.open(
                    path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600
                )
                os.fchmod(fd, 0o600)
                os.close(fd)
                logger = logging.Logger("dictadesk.diagnostics")
                handler = PrivateHandler(path, maxBytes=512 * 1024, backupCount=2)
                handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
                logger.addHandler(handler)
                if _logger:
                    for old in _logger.handlers:
                        old.close()
                _logger, _path = logger, path
            _logger.info(
                json.dumps({"event": name, "pid": os.getpid(), **metadata}, sort_keys=True)
            )
    except Exception:
        # A full disk or unavailable state directory must not break recording.
        pass


def failure(name, error, **metadata):
    frame = error.__traceback__
    while frame and frame.tb_next:
        frame = frame.tb_next
    event(
        name,
        error_type=type(error).__name__,
        location=f"{frame.tb_frame.f_code.co_name}:{frame.tb_lineno}" if frame else "",
        **metadata,
    )
