# SPDX-License-Identifier: GPL-3.0-only
"""Exact wake/command grammar; application suggestions never execute speech."""

from difflib import SequenceMatcher
from pathlib import Path
from .commands import words, percentage, recognize  # noqa: F401


def wake_matches(text, phrase):
    needle, haystack = words(phrase), words(text)
    return bool(needle) and any(
        haystack[i : i + len(needle)] == needle for i in range(len(haystack) - len(needle) + 1)
    )


def command(text, preferences=None):
    return recognize(text, preferences)


def application_names(app):
    names = {words(app.get_display_name()), words(app.get_name())}
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


def exact_application_match(query, app):
    return query in application_names(app)


def match_applications(query, applications):
    """Exact names win; partial/approximate matches require UI confirmation."""
    if not query:
        return []
    exact, suggestions = {}, {}
    spoken = " ".join(query)
    for app in applications:
        # OnlyShowIn controls menus, not whether an installed app can launch.
        # Keep explicitly hidden/helper desktop entries out of voice results.
        if hasattr(app, "get_nodisplay"):
            if app.get_nodisplay() or app.get_is_hidden():
                continue
        elif not app.should_show():
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
