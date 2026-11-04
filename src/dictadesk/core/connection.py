# SPDX-License-Identifier: GPL-3.0-only
"""An explicit metadata-only check: no microphone, audio upload or transcription."""

import json
import urllib.error
import urllib.request

from .models import REMOTE_MODELS


def test_connection(key, model):
    if not key:
        return "Enter an API key or save one in your desktop keyring first."
    if model not in REMOTE_MODELS:
        return "Choose a supported service model."
    request = urllib.request.Request(
        "https://api.openai.com/v1/models/" + model,
        headers={"Authorization": "Bearer " + key},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            data = json.load(response)
        if not isinstance(data, dict) or data.get("id") != model:
            return "The service returned an unexpected response. Try again later."
        return "Connected; key accepted and model visible. Transcription permissions and billing balance were not tested. No audio was sent."
    except urllib.error.HTTPError as exc:
        return {
            401: "The API key was rejected. Check the key and try again.",
            403: "Access denied. Check your API key permissions and project access.",
            404: "The selected model is unavailable to this account.",
            429: "The service is limiting requests. Check account limits and try later.",
        }.get(exc.code, f"The service returned HTTP {exc.code}. Try again later.")
    except Exception:
        # Never surface remote response bodies, headers or exception text containing keys.
        return "Could not connect. Check your network and try again."
