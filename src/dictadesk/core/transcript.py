# SPDX-License-Identifier: GPL-3.0-only
"""The current result lives in memory; vocabulary substitutions never cascade."""

import re
from dataclasses import dataclass


COMMANDS = {
    "dot dot dot": "…",
    "new paragraph": "\n\n",
    "new line": "\n",
    "tab": "\t",
    "question mark": "?",
    "exclamation mark": "!",
    "exclamation point": "!",
    "full stop": ".",
    "period": ".",
    "comma": ",",
    "semicolon": ";",
    "colon": ":",
}
for name, opening, closing in (
    ("parenthesis", "(", ")"),
    ("bracket", "[", "]"),
    ("brace", "{", "}"),
):
    for alias in ("left", "open"):
        COMMANDS[f"{alias} {name}"] = opening
    for alias in ("right", "close"):
        COMMANDS[f"{alias} {name}"] = closing
COMMAND_PATTERN = re.compile(
    r"(?<![\w./@-])(?:"
    + "|".join(
        r"[\s,.!?]+".join(re.escape(word) for word in phrase.split())
        for phrase in sorted(COMMANDS, key=len, reverse=True)
    )
    + r")(?![\w/@-]|\.[\w])",
    re.IGNORECASE,
)


def punctuate(text):
    """Explicit English command mode. Literal command words can be restored via Undo."""
    result = ""
    end = 0
    pending = None
    for match in COMMAND_PATTERN.finditer(text):
        chunk = text[end : match.start()]
        chunk = following(chunk, pending)
        command = " ".join(re.findall(r"\w+", match[0].lower()))
        mark = COMMANDS[command]
        # ASR can put punctuation around the spoken command itself. Replace that
        # adjacent punctuation, without normalizing unrelated prose or URLs.
        if mark in "()[]{}":
            result += chunk.rstrip(" \t\r\n")
            if mark in "([{":
                if result and result[-1] not in " \t\r\n([{":
                    result += " "
            result += mark
        else:
            result += chunk.rstrip(" \t\r\n.,;:!?…") + mark
        pending = mark
        end = match.end()
    if pending is None:
        return text
    return result + following(text[end:], pending)


def following(chunk, pending):
    if pending is None or not chunk:
        return chunk
    chunk = chunk.lstrip(" \t" if pending in "()[]{}" else " \t.,;:!?…")
    if not chunk:
        return ""
    if pending in (".", "?", "!", "\n", "\n\n"):
        chunk = re.sub(r"^(\W*)(\w)", lambda m: m[1] + m[2].upper(), chunk, count=1)
    no_space = (
        pending in "([{"
        or pending.startswith(("\n", "\t"))
        or chunk.startswith("\n")
        or (pending in ")]}" and chunk[0] in ").,;:!?…]}")
    )
    return ("" if no_space else " ") + chunk


def correct(text, entries):
    # Personal rules take priority; all replacements use the original text so
    # neither built-in nor personal substitutions cascade into another rule.
    mapping = {"dictadesk": "DictaDesk"}
    mapping.update({source.casefold(): replacement for source, replacement in entries})
    alternatives = "|".join(
        re.escape(source)
        for source, _ in sorted(entries, key=lambda pair: len(pair[0]), reverse=True)
    )
    pattern = r"(?<![\w./@-])dictadesk(?![\w/@-]|\.[\w])"
    if alternatives:
        pattern = r"(?<!\w)(?:" + alternatives + r")(?!\w)|" + pattern
    return re.sub(
        pattern,
        lambda match: mapping[match[0].casefold()],
        text,
        flags=re.IGNORECASE,
    )


def explicit_ending(original, enabled):
    commands = list(COMMAND_PATTERN.finditer(original)) if enabled else []
    if not commands:
        return False
    last = commands[-1]
    command = " ".join(re.findall(r"\w+", last[0].lower()))
    return COMMANDS[command] in (".", "?", "!", "…", "\n", "\n\n") and not original[
        last.end() :
    ].strip(" \t\r\n.,;:!?…")


def provisional(text, original, spoken_punctuation=False):
    """Hold a model's final punctuation until a real sentence boundary arrives."""
    if explicit_ending(original, spoken_punctuation):
        return text
    return re.sub(r"[.!?…]+[\"'”’)}\]]*$", "", text).rstrip()


def continuation(previous, fragment):
    """Only append: never revise text already sent to another application."""
    if not fragment or not previous:
        return fragment
    if previous.endswith((".", "?", "!", "\n")):
        fragment = re.sub(r"^(\W*)(\w)", lambda m: m[1] + m[2].upper(), fragment, count=1)
    if previous[-1] in " \t\n([{" or fragment[0] in "\n\t.,;:!?…)]}":
        return fragment
    return " " + fragment


@dataclass
class Transcript:
    original: str = ""
    corrected: str = ""
    undo: bool = False

    @property
    def text(self):
        return self.original if self.undo else self.corrected

    @property
    def can_undo(self):
        return self.original != self.corrected

    def replace(self, text, vocabulary, spoken_punctuation=False):
        self.original = text
        self.corrected = correct(punctuate(text) if spoken_punctuation else text, vocabulary)
        self.undo = False
