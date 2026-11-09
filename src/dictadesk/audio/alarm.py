# SPDX-License-Identifier: GPL-3.0-only
"""A short, cancellable local timer alarm with no model or external player."""

from array import array
import math
import threading
import time

from .speech import play


class Alarm:
    def __init__(self, output=play):
        self.output = output
        self.thread = None
        self.stop_event = threading.Event()
        self.problem = ""

    @property
    def busy(self):
        return bool(self.thread and self.thread.is_alive())

    def stop(self):
        self.stop_event.set()

    def wait(self, cancelled):
        deadline = time.monotonic() + 5
        while self.busy:
            self.thread.join(0.05)
            if cancelled.is_set():
                raise InterruptedError()
            if self.busy and time.monotonic() >= deadline:
                raise TimeoutError(
                    "Timer sound did not release the audio device. Try recording again."
                )

    def start(self):
        if self.busy:
            return False
        self.stop_event = stop = threading.Event()
        self.problem = ""
        rate = 16000
        pulse = array(
            "h",
            (
                int(
                    6500 * math.sin(2 * math.pi * 880 * i / rate) * math.sin(math.pi * i / 4000)
                )
                for i in range(4000)
            ),
        ).tobytes()
        data = (pulse + bytes(8000)) * 3

        def work():
            try:
                for _ in range(3):
                    self.output(rate, data, stop)
                    if stop.wait(0.5):
                        return
            except InterruptedError:
                pass
            except Exception:
                if stop.is_set():
                    return
                self.problem = "Timer finished, but its alarm sound could not be played. Check the sound output."

        self.thread = threading.Thread(target=work, daemon=True)
        self.thread.start()
        return True
