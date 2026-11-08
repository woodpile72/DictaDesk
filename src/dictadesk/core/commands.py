# SPDX-License-Identifier: GPL-3.0-only
"""Deterministic system-command registry and its shared reference metadata."""

from dataclasses import dataclass
import re
import unicodedata

from .application_roles import APPLICATION_ROLES

CATEGORIES = (
    "Audio",
    "Display and Power",
    "Media Playback",
    "Screenshots",
    "Window Management",
    "Applications",
    "Clipboard",
    "Dictation Formatting",
)


def words(text):
    text = unicodedata.normalize("NFKC", text).casefold()
    return tuple("".join(c if c.isalnum() else " " for c in text).split())


def percentage(tokens, *, zero=False):
    small = dict(
        zip(
            (
                "zero one two three four five six seven eight nine ten eleven twelve thirteen "
                "fourteen fifteen sixteen seventeen eighteen nineteen"
            ).split(),
            range(20),
        )
    )
    tens = dict(
        zip("twenty thirty forty fifty sixty seventy eighty ninety".split(), range(20, 100, 10))
    )
    value = None
    if len(tokens) == 1:
        token = tokens[0]
        if token.isascii() and token.isdigit() and len(token) <= 3:
            value = int(token)
        else:
            value = small.get(token, tens.get(token))
    elif tokens == ("one", "hundred"):
        value = 100
    elif (
        len(tokens) == 2
        and tokens[0] in tens
        and tokens[1] in small
        and 0 < small[tokens[1]] < 10
    ):
        value = tens[tokens[0]] + small[tokens[1]]
    return value if value is not None and (0 if zero else 1) <= value <= 100 else None


@dataclass(frozen=True)
class Command:
    ident: str
    category: str
    name: str
    description: str
    phrases: tuple
    handler: str
    arguments: tuple = ()
    parameter: str = ""  # percentage, optional_percentage, application
    default: str = ""
    examples: tuple = ()
    capability: str = ""


def entry(ident, category, name, description, phrases, handler, arguments=(), **kwargs):
    return Command(
        ident, category, name, description, tuple(phrases), handler, arguments, **kwargs
    )


REGISTRY = (
    entry(
        "volume-up",
        "Audio",
        "Volume up",
        "Increase by {volume_step} percentage points by default, or by the spoken amount.",
        ("volume up",),
        "volume",
        ("up",),
        parameter="optional_percentage",
        default="volume_step",
        examples=("Volume up", "Volume up fifteen percent"),
        capability="audio",
    ),
    entry(
        "volume-down",
        "Audio",
        "Volume down",
        "Decrease by {volume_step} percentage points by default, or by the spoken amount.",
        ("volume down",),
        "volume",
        ("down",),
        parameter="optional_percentage",
        default="volume_step",
        examples=("Volume down", "Volume down 15%"),
        capability="audio",
    ),
    entry(
        "volume-max",
        "Audio",
        "Volume max",
        "Set output volume to 100%.",
        ("volume max",),
        "volume",
        ("set", 100),
        capability="audio",
    ),
    entry(
        "volume-set",
        "Audio",
        "Set volume",
        "Set absolute output volume to the spoken percentage (0–100%).",
        ("volume",),
        "volume",
        ("set",),
        parameter="percentage",
        examples=("Volume 50%",),
        capability="audio",
    ),
    entry(
        "volume-mute",
        "Audio",
        "Mute output",
        "Mute speakers.",
        ("volume mute",),
        "volume",
        ("mute",),
        capability="audio",
    ),
    entry(
        "volume-unmute",
        "Audio",
        "Unmute output",
        "Unmute speakers.",
        ("volume unmute",),
        "volume",
        ("unmute",),
        capability="audio",
    ),
    entry(
        "brightness-up",
        "Display and Power",
        "Brightness up",
        "Increase supported backlight brightness by {brightness_step} points, or the spoken amount.",
        ("brightness up",),
        "brightness",
        ("up",),
        parameter="optional_percentage",
        default="brightness_step",
        examples=("Brightness up", "Brightness up 20%"),
        capability="brightness",
    ),
    entry(
        "brightness-down",
        "Display and Power",
        "Brightness down",
        "Decrease supported backlight brightness by {brightness_step} points, or the spoken amount.",
        ("brightness down",),
        "brightness",
        ("down",),
        parameter="optional_percentage",
        default="brightness_step",
        capability="brightness",
    ),
    entry(
        "brightness-set",
        "Display and Power",
        "Set brightness",
        "Set absolute backlight brightness (0–100%).",
        ("brightness",),
        "brightness",
        ("set",),
        parameter="percentage",
        examples=("Brightness 70%",),
        capability="brightness",
    ),
    entry(
        "lock",
        "Display and Power",
        "Lock screen",
        "Request the desktop session lock.",
        ("lock screen",),
        "lock",
        capability="screen",
    ),
    entry(
        "screensaver",
        "Display and Power",
        "Screen saver",
        "Activate the desktop screen saver; desktop lock settings still apply.",
        ("screen saver", "screensaver"),
        "screensaver",
        capability="screen",
    ),
    entry(
        "screen-off",
        "Display and Power",
        "Screen off",
        "Turn off X11 displays using DPMS, without suspending.",
        ("screen off",),
        "screen-off",
        capability="dpms",
    ),
    entry(
        "suspend",
        "Display and Power",
        "Suspend computer",
        "Ask the system to suspend, subject to its normal authorization policy.",
        ("suspend computer",),
        "suspend",
        capability="suspend",
    ),
    entry(
        "dnd-on",
        "Display and Power",
        "Do not disturb on",
        "Disable desktop notification popups.",
        ("do not disturb on",),
        "dnd",
        (True,),
        capability="dnd",
    ),
    entry(
        "dnd-off",
        "Display and Power",
        "Do not disturb off",
        "Enable desktop notification popups.",
        ("do not disturb off",),
        "dnd",
        (False,),
        capability="dnd",
    ),
    *(
        entry(
            "media-" + ident,
            "Media Playback",
            phrase.title(),
            description,
            (phrase,),
            "media",
            (method,),
            capability="media",
        )
        for ident, phrase, method, description in (
            ("play", "play", "Play", "Resume or start playback."),
            ("pause", "pause", "Pause", "Pause playback."),
            ("toggle", "play pause", "PlayPause", "Toggle playback."),
            ("next", "next track", "Next", "Skip to the next track."),
            ("previous", "previous track", "Previous", "Return to the previous track."),
            ("stop", "stop playback", "Stop", "Stop media playback."),
        )
    ),
    entry(
        "screenshot",
        "Screenshots",
        "Take screenshot",
        "Capture the full screen using the desktop's screenshot tool and normal save behavior.",
        ("take screenshot",),
        "screenshot",
        ("screen",),
        capability="screenshot",
    ),
    entry(
        "screenshot-window",
        "Screenshots",
        "Screenshot window",
        "Capture the application window remembered at wake-word activation.",
        ("screenshot window",),
        "screenshot",
        ("window",),
        capability="screenshot-window",
    ),
    *(
        entry(
            "window-" + ident,
            "Window Management",
            phrase.title(),
            description,
            (phrase,),
            "window",
            (ident,),
            capability="window",
        )
        for ident, phrase, description in (
            ("desktop", "show desktop", "Show the desktop."),
            (
                "switch",
                "switch window",
                "Switch to the previous window, relative to the remembered window.",
            ),
            ("maximize", "maximize window", "Maximize the remembered application window."),
            ("minimize", "minimize window", "Minimize the remembered application window."),
            (
                "left",
                "window left",
                "Fit the remembered window into the left half of its monitor's work area.",
            ),
            (
                "right",
                "window right",
                "Fit the remembered window into the right half of its monitor's work area.",
            ),
            (
                "close",
                "close window",
                "Request a normal close; applications can prompt about unsaved work.",
            ),
        )
    ),
    *(
        entry(
            "open-" + role.ident,
            "Applications",
            "Open " + role.name,
            f"Open the preferred {role.name} configured in your desktop's Preferred Applications settings.",
            (f"{verb} {name}" for verb in ("open", "launch") for name in role.spoken_names),
            "open",
            tuple(role.aliases[0].split()),
            examples=tuple("Open " + name for name in role.aliases[:2]),
            capability="application-" + role.ident,
        )
        for role in APPLICATION_ROLES
    ),
    entry(
        "open",
        "Applications",
        "Open application",
        "Launch a specific installed application by its display name, executable name or desktop-entry ID (for example Nemo, even when its menu label is Files). Ambiguous or approximate names require confirmation.",
        ("open", "launch"),
        "open",
        parameter="application",
        examples=("Open Firefox", "Open Nemo", "Open Xed"),
        capability="applications",
    ),
    entry(
        "pause-commands",
        "Audio",
        "Pause voice commands",
        "Pause wake-word listening for 30 minutes by default; dictation remains available. Resume automatically or from the tray.",
        ("pause voice commands", "suspend voice commands"),
        "pause-commands",
        parameter="duration",
        examples=("Pause voice commands", "Pause voice commands for half an hour"),
        capability="commands",
    ),
    entry(
        "copy-that",
        "Clipboard",
        "Copy that",
        "Copy the last completed dictation; commands never replace it.",
        ("copy that",),
        "clipboard",
        ("copy",),
        capability="clipboard",
    ),
    entry(
        "paste-that",
        "Clipboard",
        "Paste that",
        "Insert the last completed dictation into the field remembered at wake-word activation, with safe recovery if unavailable.",
        ("paste that",),
        "clipboard",
        ("paste",),
        capability="paste",
    ),
    entry(
        "clear-clipboard",
        "Clipboard",
        "Clear clipboard",
        "Clear clipboard contents without deleting the last dictation.",
        ("clear clipboard",),
        "clipboard",
        ("clear",),
        capability="clipboard",
    ),
)


def registry():
    # Formatting is documented from the same map used by the existing dictation
    # postprocessor. These entries never participate in system-command matching.
    from .transcript import COMMANDS

    formatting = tuple(
        entry(
            "format-" + phrase.replace(" ", "-"),
            "Dictation Formatting",
            phrase.title(),
            f"Insert {phrase} in dictation when Spoken Punctuation is enabled.",
            (phrase,),
            "format",
            (mark,),
            capability="format",
        )
        for phrase, mark in COMMANDS.items()
    )
    return REGISTRY + formatting


def recognize(text, preferences=None):
    tokens = words(text)
    # Observed ASR substitutions for this single command. Match only the
    # entire utterance; never rewrite application names or arbitrary sentences.
    if tokens in {
        ("screenshaver",),
        ("screen", "shaver"),
        ("screensaber",),
        ("screen", "saber"),
    }:
        tokens = ("screensaver",)
    if tokens == ("cancel",):
        return "cancel", ()
    raw = unicodedata.normalize("NFKC", text).casefold().strip().rstrip(".!?")
    numeric_invalid = bool(re.search(r"[-+−]\s*\d|\d[.,]\d", raw))
    for spec in REGISTRY:
        for phrase in spec.phrases:
            prefix = words(phrase)
            if not spec.parameter:
                if tokens == prefix:
                    return spec.handler, spec.arguments
                continue
            if tokens[: len(prefix)] != prefix:
                continue
            rest = tokens[len(prefix) :]
            if spec.parameter == "application":
                if rest:
                    return spec.handler, rest
                continue
            if spec.parameter == "duration":
                if numeric_invalid:
                    continue
                if not rest:
                    return spec.handler, (30,)
                if rest[:1] == ("for",):
                    rest = rest[1:]
                if rest in (("half", "an", "hour"), ("half", "hour")):
                    return spec.handler, (30,)
                if rest[-1:] in (("minute",), ("minutes",)):
                    rest = rest[:-1]
                    duration = percentage(rest)
                    if duration:
                        return spec.handler, (duration,)
                continue
            if numeric_invalid:
                continue
            if not rest and spec.parameter == "optional_percentage":
                return spec.handler, spec.arguments + (getattr(preferences, spec.default, 5),)
            if rest[:1] == ("by",):
                rest = rest[1:]
            if rest[-2:] == ("percentage", "points"):
                rest = rest[:-2]
            elif rest[-1:] in (("percent",), ("points",)):
                rest = rest[:-1]
            value = percentage(rest, zero=spec.parameter == "percentage")
            if value is not None:
                return spec.handler, spec.arguments + (value,)
    return "unknown", ()
