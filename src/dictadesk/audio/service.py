# SPDX-License-Identifier: GPL-3.0-only
"""Cancellable process boundary for model loading and recognition."""

import json
import os
import queue
import select
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from ..core.diagnostics import WORKER_PREFIX, event, failure


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
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            start_new_session=True,
            env={**os.environ, "DICTADESK_WORKER_DIAGNOSTICS": "1"},
        )
        os.set_blocking(process.stdin.fileno(), False)
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

        def diagnostics():
            try:
                while line := process.stderr.readline(2048):
                    if line.startswith(WORKER_PREFIX):
                        try:
                            value = json.loads(line[len(WORKER_PREFIX) :])
                            name = value.pop("event")
                            allowed = {
                                "operation",
                                "error_type",
                                "location",
                                "samples",
                                "rate",
                                "peak",
                                "rms",
                                "characters",
                                "elapsed",
                                "local_model",
                                "device",
                            }
                            if isinstance(name, str) and name.startswith("worker."):
                                event(
                                    name,
                                    **{
                                        k: v
                                        for k, v in value.items()
                                        if k in allowed
                                        and isinstance(v, (str, int, float, bool))
                                    },
                                )
                        except (ValueError, TypeError, KeyError):
                            pass
            finally:
                process.stderr.close()

        threading.Thread(target=diagnostics, daemon=True).start()
        self.process, self.replies = process, replies
        event("worker.spawned", worker_pid=process.pid)
        return process, replies

    def request(self, payload, cancelled, timeout=180):
        started = time.monotonic()
        deadline = started + timeout
        # Waiting for a preview or another inference also counts toward timeout.
        while not self.serial.acquire(timeout=0.05):
            if cancelled.is_set():
                raise InterruptedError()
            if time.monotonic() >= deadline:
                raise TimeoutError("Speech processing is busy. You can try again.")
        try:
            if cancelled.is_set():
                raise InterruptedError()
            with self.guard:
                if self.process is None or self.process.poll() is not None:
                    process, replies = self._spawn()
                else:
                    process, replies = self.process, self.replies
            try:
                event("recognition.submitted", operation=payload["op"], timeout=timeout)
                # PCM requests can exceed the pipe buffer. A worker which stops
                # reading must not trap cancellation in a blocking write.
                outgoing = memoryview((json.dumps(payload) + "\n").encode())
                while outgoing:
                    if cancelled.is_set():
                        raise InterruptedError()
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Speech processing timed out. You can try again.")
                    if not select.select([], [process.stdin], [], 0.05)[1]:
                        continue
                    try:
                        outgoing = outgoing[os.write(process.stdin.fileno(), outgoing) :]
                    except BlockingIOError:
                        pass
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
                    if "text" in answer and not isinstance(answer["text"], str):
                        raise RuntimeError("The speech worker returned an invalid transcript.")
                    event(
                        "recognition.finished",
                        operation=payload["op"],
                        characters=len(answer.get("text", "")),
                        elapsed=round(time.monotonic() - started, 3),
                    )
                    return answer
                raise InterruptedError()
            except BaseException as exc:
                failure(
                    "recognition.failed",
                    exc,
                    operation=payload["op"],
                    elapsed=round(time.monotonic() - started, 3),
                    worker_exit=process.poll(),
                )
                self.close()
                raise
        finally:
            self.serial.release()

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
        event("worker.closed", worker_pid=process.pid, exit_code=process.returncode)


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
