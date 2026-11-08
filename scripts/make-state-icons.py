#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Original, mask-free state icons drawn on the panel's 16-pixel grid."""

from pathlib import Path

out = Path(__file__).resolve().parents[1] / "src/dictadesk/resources"
# Every state uses this SAME full-height microphone, leaving a fixed pocket
# for a small status badge. Changing state must never move or resize the mic.
# Filled contours preserve negative space in Cinnamon's symbolic loader.
body = '<rect x="3" y="0" width="4" height="9" rx="2"/><path d="M0 6h2v2a3 3 0 0 0 6 0V6h2v2a5 5 0 0 1-4 4.9V15h2v1H2v-1h2v-2.1A5 5 0 0 1 0 8Z"/>'
badges = {
    "idle": "",
    "wake": '<circle class="success" fill="#3584e4" cx="13" cy="13" r="3"/>',
    "listening": '<circle class="error" fill="#cc0000" cx="13" cy="13" r="3"/>',
    "loading": '<circle class="warning" fill="#f57900" cx="13" cy="13" r="3"/>',
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
                "wake": "#eeeeec",
                "listening": "#eeeeec",
                "loading": "#eeeeec",
                "processing": "#e5a50a",
                "unavailable": "#9a9996",
            }[state]
        )
        icon_state = "recording" if state == "listening" else state
        name = f"dictadesk-panel-{icon_state}" + ("-symbolic" if symbolic else "-color")
        (out / (name + ".svg")).write_text(
            f'<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16" fill="{color}">{body}{badge}</svg>'
        )
