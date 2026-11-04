# SPDX-License-Identifier: GPL-3.0-only
"""Cancellable process boundary for model loading and recognition."""

import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path


class Recognizer:
    def __init__(self, command=None):
        root = Path(__file__).resolve().parents[2]
        paths = [str(root)]
        if (root / "vendor").is_dir():
            paths.append(str(root / "vendor"))
        # A launcher changes sys.path only in its own process. Bootstrap the
        # worker explicitly so installed packages never depend on PYTHONPATH.
        bootstrap = (
            "import os,sys; "
            f"sys.path[:0] = {paths!r}; "
            "os.umask(0o077); "
            "from dictadesk.audio.worker import main; main()"
        )
        self.command = command or [sys.executable, "-c", bootstrap]
        self.process = None
        self.loaded = False
        self.serial = threading.Lock()
        self.guard = threading.Lock()

    def _spawn(self):
        self.loaded = False
        process = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            start_new_session=True,
        )
        replies = queue.Queue()

        def collect():
            try:
                for line in process.stdout:
                    try:
                        reply = json.loads(line)
                        if not isinstance(reply, dict):
                            raise ValueError("Invalid reply type")
                        replies.put(reply)
                    except ValueError:
                        replies.put({"error": "The speech worker sent an invalid response."})
            finally:
                replies.put({"error": "The speech worker stopped unexpectedly."})
                process.stdout.close()

        threading.Thread(target=collect, daemon=True).start()
        self.process, self.replies = process, replies
        return process, replies

    def request(self, payload, cancelled, timeout=180):
        with self.serial:
            if cancelled.is_set():
                raise InterruptedError()
            with self.guard:
                if self.process is None or self.process.poll() is not None:
                    process, replies = self._spawn()
                else:
                    process, replies = self.process, self.replies
            try:
                process.stdin.write(json.dumps(payload) + "\n")
                process.stdin.flush()
                deadline = time.monotonic() + timeout
                while not cancelled.is_set():
                    if time.monotonic() > deadline:
                        raise TimeoutError("Speech processing timed out. You can try again.")
                    try:
                        answer = replies.get(timeout=0.05)
                    except queue.Empty:
                        continue
                    if cancelled.is_set():
                        break
                    if "error" in answer:
                        raise RuntimeError(answer["error"])
                    if payload["op"] == "load":
                        self.loaded = True
                    return answer
                raise InterruptedError()
            except BaseException:
                self.close()
                raise

    def close(self):
        with self.guard:
            process, self.process = self.process, None
            self.loaded = False
        if process is None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=0.6)
        except subprocess.TimeoutExpired:
            pass
        # Also remove descendants which survived their parent.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=2)
        process.stdin.close()


def compute_capabilities(cancelled=None):
    """Probe in a disposable process so driver failures cannot crash GTK."""
    worker = Recognizer()
    try:
        return worker.request(
            {"op": "capabilities"}, cancelled or threading.Event(), timeout=20
        )
    except InterruptedError:
        raise
    except Exception:
        return {"cuda": False, "reason": "CUDA could not be verified in this installation."}
    finally:
        worker.close()
