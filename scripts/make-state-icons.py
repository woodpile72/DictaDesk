#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Original, mask-free state icons drawn on the panel's 16-pixel grid."""

from pathlib import Path

out = Path(__file__).resolve().parents[1] / "src/dictadesk/resources"
# Filled contours preserve their negative space in Cinnamon's symbolic loader.
# No strokes, masks or currentColor: all visible geometry is explicit.
body = '<rect x="6" y="0" width="4" height="9" rx="2"/><path d="M1 6h2v1a5 5 0 0 0 10 0V6h2v1a7 7 0 0 1-6 6.93V15h3v1H4v-1h3v-1.07A7 7 0 0 1 1 7Z"/>'
compact = '<rect x="3" y="0" width="4" height="9" rx="2"/><path d="M0 6h2v2a3 3 0 0 0 6 0V6h2v2a5 5 0 0 1-4 4.9V15h2v1H2v-1h2v-2.1A5 5 0 0 1 0 8Z"/>'
# Leave a clear lower-right pocket for the recording badge, while keeping
# the full-height microphone; shift it one pixel left for the 8-pixel badge.
recording = '<rect x="5" y="0" width="4" height="9" rx="2"/><path d="M0 6h2v1a5 5 0 0 0 5 5v4H3v-1h3v-1.07A7 7 0 0 1 0 7Z M12 6h2v1h-2Z"/>'
badges = {
    "idle": "",
    "listening": '<circle class="error" fill="#cc0000" cx="12" cy="12" r="4"/>',
    "loading": '<circle class="warning" fill="#f57900" cx="12" cy="12" r="4"/>',
    "processing": '<path d="M13.5 11a2.5 2.5 0 1 1-2.5 2.5h1.5a1 1 0 1 0 1-1Z"/>',
    "unavailable": '<path d="m12 11 1.5 1.5L15 11l1 1-1.5 1.5L16 15l-1 1-1.5-1.5L12 16l-1-1 1.5-1.5L11 12Z"/>',
}
for state, badge in badges.items():
    for symbolic in (True, False):
        color = (
            "#eeeeec"
            if symbolic
            else {
                "idle": "#2ec27e",
                "listening": "#eeeeec",
                "loading": "#eeeeec",
                "processing": "#e5a50a",
                "unavailable": "#9a9996",
            }[state]
        )
        icon_state = "recording" if state == "listening" else state
        name = f"dictadesk-panel-{icon_state}" + ("-symbolic" if symbolic else "-color")
        geometry = (
            body
            if state == "idle"
            else recording
            if state in ("listening", "loading")
            else compact
        )
        (out / (name + ".svg")).write_text(
            f'<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16" fill="{color}">{geometry}{badge}</svg>'
        )
