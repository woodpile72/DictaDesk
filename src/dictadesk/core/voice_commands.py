# SPDX-License-Identifier: GPL-3.0-only
"""Bounded wake matching and exact command grammar; suggestions never execute speech."""

from difflib import SequenceMatcher
import re
import unicodedata
from pathlib import Path
from .commands import words, percentage, recognize  # noqa: F401


def wake_activation(text, phrase):
    normalized = unicodedata.normalize("NFKC", text).casefold()
    spans = list(re.finditer(r"[^\W_]+", normalized))
    needle, haystack = words(phrase), tuple(span[0] for span in spans)
    if not needle:
        return None, ""
    for index in range(len(haystack) - len(needle) + 1):
        if haystack[index : index + len(needle)] == needle:
            # Keep punctuation/numeric signs in the suffix so command validation
            # can still reject e.g. a negative percentage or multiple commands.
            suffix = (
                normalized[spans[index + len(needle) - 1].end() :]
                .lstrip()
                .lstrip(",.:!?")
                .lstrip()
            )
            return "exact", suffix
    # A short, rare wake word can lose an unstressed ending or one interior
    # consonant in ordinary ASR. Allow ONE such token, only when the ENTIRE
    # utterance is the configured phrase. Never search sentences approximately,
    # accept arbitrary prefixes, change an initial sound/vowel, or loosen commands.
    if len(needle) != len(haystack):
        return None, ""
    changes = 0
    vowels = frozenset("aeiouy")
    for expected, heard in zip(needle, haystack):
        if expected == heard:
            continue
        changes += 1
        if (
            changes > 1
            or len(expected) < 6
            or expected[:3] != heard[:3]
            or not expected.isascii()
            or not expected.isalpha()
            or not heard.isascii()
            or not heard.isalpha()
        ):
            return None, ""
        if len(expected) >= 7 and expected.endswith("er") and heard == expected[:-2]:
            continue
        if len(expected) != len(heard) or expected[-2:] != heard[-2:]:
            return None, ""
        different = [(a, b) for a, b in zip(expected, heard) if a != b]
        if len(different) != 1 or any(c in vowels for pair in different for c in pair):
            return None, ""
    return ("limited" if changes else None), ""


def wake_match_kind(text, phrase):
    return wake_activation(text, phrase)[0]


def wake_matches(text, phrase):
    return wake_match_kind(text, phrase) is not None


def command(text, preferences=None):
    return recognize(text, preferences)


def application_names(app):
    names = {words(app.get_display_name()), words(app.get_name())}
    ident = app.get_id()
    if ident:
        stem = Path(ident).name.removesuffix(".desktop")
        names.add(words(stem))
        # Flatpak/wrapper entries often expose only a generic menu label and
        # executable. Their desktop ID still identifies the actual application.
        if "." in stem:
            names.add(words(stem.rsplit(".", 1)[-1]))
    executable = getattr(app, "get_executable", lambda: None)()
    if executable:
        executable = Path(executable).name
        # Interpreter/launcher names do not identify the application itself.
        if executable not in {
            "env",
            "sh",
            "bash",
            "python",
            "python3",
            "flatpak",
            "snap",
            "electron",
            "java",
            "wine",
            "xdg-open",
            "gio",
        }:
            names.add(words(executable))
    return {name for name in names if name}


def launchable_application(app):
    # OnlyShowIn controls menus, not whether an installed app can launch.
    if hasattr(app, "get_nodisplay"):
        return not (app.get_nodisplay() or app.get_is_hidden())
    return app.should_show()


def exact_application_match(query, app):
    return query in application_names(app)


def match_applications(query, applications):
    """Exact names win; partial/approximate matches require UI confirmation."""
    if not query:
        return []
    exact, suggestions = {}, {}
    spoken = " ".join(query)
    for app in applications:
        if not launchable_application(app):
            continue
        ident = app.get_id()
        if not ident:
            continue
        names = application_names(app)
        if query in names:
            exact[ident] = app
            continue
        score = 0.0
        for name in names:
            if name[: len(query)] == query:
                score = max(score, 1.0)
            elif len(spoken) >= 4:
                # Also compare the first words, e.g. caliber -> Calibre Reader.
                for candidate in (name, name[: len(query)]):
                    ratio = SequenceMatcher(None, spoken, " ".join(candidate)).ratio()
                    if ratio >= 0.8:
                        score = max(score, ratio)
        if score:
            suggestions[ident] = (score, app)
    if exact:
        return sorted(exact.values(), key=lambda app: app.get_display_name().casefold())
    ranked = sorted(
        suggestions.values(), key=lambda pair: (-pair[0], pair[1].get_display_name().casefold())
    )
    return [app for _, app in ranked]


def diagnostic(heard, query=None):
    """Short plain text for transient UI feedback, never background transcripts."""

    def compact(text):
        text = " ".join("".join(c if c.isprintable() else " " for c in text).split())
        return text if len(text) <= 160 else text[:159] + "…"

    text = compact(heard)
    lines = [f"Heard: “{text}”" if text else "Heard: No command recognized."]
    if query:
        lines.append(f"Looking for application: “{compact(' '.join(query))}”")
    return "\n".join(lines)


def choice_number(text):
    tokens = words(text)
    if tokens in (("cancel",), ("more",), ("other",), ("next",), ("next", "page")):
        return "cancel" if tokens == ("cancel",) else "more"
    if tokens and tokens[0] in ("number", "option"):
        tokens = tokens[1:]
    numbers = {
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "1": 1,
        "2": 2,
        "3": 3,
        "4": 4,
        "5": 5,
    }
    return numbers.get(tokens[0]) if len(tokens) == 1 else None
