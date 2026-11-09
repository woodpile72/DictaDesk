# SPDX-License-Identifier: GPL-3.0-only
"""One local countdown, persisted atomically and measured across suspend."""

import json
import math
from pathlib import Path
import time

from .settings import directory, private_write

MAX_SECONDS = 24 * 60 * 60


def boot_time():
    return time.clock_gettime(time.CLOCK_BOOTTIME)


def boot_identity():
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return None


def duration(tokens):
    from .commands import percentage

    if tokens in (("half", "an", "hour"), ("half", "hour")):
        return 1800
    units = {
        "hour": 3600,
        "hours": 3600,
        "minute": 60,
        "minutes": 60,
        "second": 1,
        "seconds": 1,
    }
    total, previous, number = 0, 3601, []
    for index, token in enumerate(tokens):
        if (
            token == "and"
            and not number
            and total
            and index + 1 < len(tokens)
            and tokens[index + 1] != "and"
        ):
            continue
        if token not in units:
            number.append(token)
            continue
        scale = units[token]
        if not number or scale >= previous:
            return None
        value = 1 if number in (["a"], ["an"]) else percentage(tuple(number), zero=True)
        if (
            value is None
            and len(number) == 1
            and number[0].isascii()
            and number[0].isdigit()
            and len(number[0]) <= 5
        ):
            value = int(number[0])
        if value is None and len(number) >= 2 and number[1] == "hundred":
            hundreds = percentage(tuple(number[:1]))
            rest = number[2:]
            if rest[:1] == ["and"]:
                rest = rest[1:]
            remainder = percentage(tuple(rest), zero=True) if rest else 0
            if hundreds and hundreds <= 9 and remainder is not None:
                value = hundreds * 100 + remainder
        if value is None:
            return None
        total += value * scale
        previous, number = scale, []
    return total if not number and 0 < total <= MAX_SECONDS and tokens[-1] in units else None


def describe(seconds):
    seconds = max(0, math.ceil(seconds))
    parts = []
    for unit, size in (("hour", 3600), ("minute", 60), ("second", 1)):
        count, seconds = divmod(seconds, size)
        if count:
            parts.append(f"{count} {unit}" + ("s" if count != 1 else ""))
    return " ".join(parts) or "0 seconds"


class Countdown:
    def __init__(self, path=None, *, clock=boot_time, wall=time.time, boot_id=None):
        self.path = path or directory("STATE") / "timer.json"
        self.clock, self.wall = clock, wall
        self.boot_id = boot_id or boot_identity()
        self.deadline = self.end = None
        self.seconds = 0
        self.elapsed = False
        self.problem = ""
        try:
            data = json.loads(self.path.read_text())
            if data is not None:
                seconds, end, elapsed = data["seconds"], data["end"], data["elapsed"]
                if (
                    type(seconds) is not int
                    or not 1 <= seconds <= MAX_SECONDS
                    or type(end) not in (int, float)
                    or not math.isfinite(end)
                    or type(elapsed) is not bool
                ):
                    raise ValueError("Invalid timer state")
                self.seconds, self.end, self.elapsed = seconds, end, elapsed
                boot_end = data.get("boot_end")
                if (
                    self.boot_id
                    and data.get("boot_id") == self.boot_id
                    and type(boot_end) in (int, float)
                    and math.isfinite(boot_end)
                ):
                    self.deadline = clock() + min(seconds, max(0, boot_end - clock()))
                else:
                    self.deadline = clock() + min(seconds, max(0, end - wall()))
        except FileNotFoundError:
            pass
        except (ValueError, KeyError, TypeError, OSError):
            self.problem = "Saved timer could not be read. Cancel the timer to reset it."

    @property
    def exists(self):
        return self.deadline is not None

    @property
    def active(self):
        return self.exists and not self.elapsed

    def remaining(self):
        return max(0, self.deadline - self.clock()) if self.exists and not self.elapsed else 0

    def status(self):
        return self.problem or (
            "Timer finished."
            if self.elapsed
            else f"Timer: {describe(self.remaining())} remaining."
            if self.exists
            else "No timer running."
        )

    def save(self, data):
        private_write(self.path, json.dumps(data).encode())

    def start(self, seconds):
        if self.problem:
            raise ValueError(self.problem)
        if self.active:
            raise ValueError("A timer is already running. Cancel it before setting another.")
        if type(seconds) is not int or not 1 <= seconds <= MAX_SECONDS:
            raise ValueError("Choose a timer from 1 second to 24 hours.")
        end, deadline = self.wall() + seconds, self.clock() + seconds
        self.save(
            {
                "seconds": seconds,
                "end": end,
                "elapsed": False,
                "boot_id": self.boot_id,
                "boot_end": deadline,
            }
        )
        self.seconds, self.end = seconds, end
        self.deadline, self.elapsed = deadline, False

    def cancel(self):
        self.save(None)
        self.deadline = self.end = None
        self.seconds, self.elapsed, self.problem = 0, False, ""

    def poll(self):
        if not self.active or self.remaining() > 0:
            return False
        self.elapsed = True
        try:
            self.save(
                {
                    "seconds": self.seconds,
                    "end": self.end,
                    "elapsed": True,
                    "boot_id": self.boot_id,
                    "boot_end": self.deadline,
                }
            )
        except OSError:
            self.problem = "Timer finished, but its state could not be saved."
        return True
