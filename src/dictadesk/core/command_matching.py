# SPDX-License-Identifier: GPL-3.0-only
"""Rank bounded command corrections. Every suggestion needs explicit confirmation."""

from dataclasses import dataclass
import re
import unicodedata

from rapidfuzz.fuzz import ratio
from rapidfuzz.distance import Levenshtein

from .commands import REGISTRY, recognize, words

# These distinctions carry intent, not merely spelling. Never correct them.
PROTECTED = frozenset(
    """up down left right next previous back forward on off
mute unmute lock unlock close clear cancel stop start pause resume play max min
zero one two three four five six seven eight nine ten eleven twelve thirteen
fourteen fifteen sixteen seventeen eighteen nineteen twenty thirty forty fifty
sixty seventy eighty ninety hundred thousand half""".split()
)
NEGATION = frozenset(
    "no not never dont don without except neither cannot cant can’t don't".split()
)


@dataclass(frozen=True)
class Suggestion:
    label: str
    kind: str
    arguments: tuple
    score: float


def prefix_score(heard, expected):
    if len(heard) != len(expected):
        return 0
    changed = 0
    for a, b in zip(heard, expected):
        if a == b:
            continue
        if a in PROTECTED or b in PROTECTED or min(len(a), len(b)) < 4:
            return 0
        if not (a.isalpha() and b.isalpha()):
            return 0
        changed += 1
        if changed > 1 or Levenshtein.distance(a, b, score_cutoff=2) > 2:
            return 0
        if ratio(a, b) < 70:
            return 0
    return ratio(" ".join(heard), " ".join(expected)) if changed else 0


def suggest_commands(text, preferences=None):
    """Correct at most one command word; validate the untouched argument suffix."""
    if not text or len(text) > 1000 or recognize(text, preferences)[0] != "unknown":
        return []
    raw = unicodedata.normalize("NFKC", text)
    spans = list(re.finditer(r"[^\W_]+", raw))
    tokens = tuple(m[0].casefold() for m in spans)
    if not tokens:
        return []
    found = {}
    for spec in REGISTRY:
        for phrase in spec.phrases:
            prefix = words(phrase)
            n = len(prefix)
            if len(tokens) < n or (not spec.parameter and len(tokens) != n):
                continue
            if any(t in NEGATION for t in tokens[:n]):
                continue
            score = prefix_score(tokens[:n], prefix)
            if score < 82:
                continue
            # Slice the original normalized text, retaining signs, decimals and
            # query punctuation. No fuzzy edits to numbers, queries or app names.
            suffix = raw[spans[n - 1].end() :] if spec.parameter else ""
            corrected = phrase + suffix
            kind, arguments = recognize(corrected, preferences)
            if kind == "unknown":
                continue
            # The registry/parser remains authoritative; never change the verb
            # by repairing a later argument or matching just part of a sentence.
            if kind != spec.handler:
                continue
            key = (kind, arguments)
            item = Suggestion(corrected.strip().rstrip(".!?"), kind, arguments, score)
            if key not in found or score > found[key].score:
                found[key] = item
    ranked = sorted(found.values(), key=lambda item: (-item.score, item.label))
    # A small local vocabulary, not a probability estimate. Keep plausible
    # alternatives close to the leader; never execute based on this score.
    return [item for item in ranked if item.score >= ranked[0].score - 8][:4]
