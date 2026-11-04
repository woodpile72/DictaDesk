# SPDX-License-Identifier: GPL-3.0-only
"""Conservative append-only agreement between overlapping recognition passes."""

import re


class StablePrefix:
    def __init__(self, hold_words=3):
        self.hold_words = hold_words
        self.previous = []
        self.committed = ""

    def update(self, text, *, final=False, paused=False):
        """Return (uncommitted preview, revision conflict). Never rewrite a prefix."""
        if not text.startswith(self.committed):
            return text, True
        words = list(re.finditer(r"\S+", text))
        values = [word[0] for word in words]
        common = 0
        for before, after in zip(self.previous, values):
            if before != after:
                break
            common += 1
        if final:
            self.committed = text
        else:
            if paused:
                # A thinking pause is useful evidence, but recognition of the
                # sentence tail can still change when speech resumes. Show an
                # initial word even for short phrases; keep a longer tail for
                # longer phrases and vocabulary commands.
                count = max(1, len(words) - max(4, self.hold_words)) if words else 0
            else:
                count = min(common, max(0, len(words) - self.hold_words))
            if count:
                candidate = text[: words[count - 1].end()]
                if len(candidate) > len(self.committed):
                    self.committed = candidate
        self.previous = values
        return text[len(self.committed) :].lstrip(), False
