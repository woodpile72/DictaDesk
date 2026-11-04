# SPDX-License-Identifier: GPL-3.0-only
"""Secrets are stored by the desktop keyring, never in preferences."""

import os

SERVICE = "io.github.woodpile72.DictaDesk"


def read_key():
    if os.environ.get("OPENAI_API_KEY"):
        return os.environ["OPENAI_API_KEY"]
    import keyring

    return keyring.get_password(SERVICE, "openai") or ""


def save_key(value):
    import keyring

    if value:
        keyring.set_password(SERVICE, "openai", value)
    elif keyring.get_password(SERVICE, "openai"):
        keyring.delete_password(SERVICE, "openai")
