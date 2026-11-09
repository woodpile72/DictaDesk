# SPDX-License-Identifier: GPL-3.0-only
"""Explicit search providers and encoded query URLs; never interpret query text."""

import re
from urllib.parse import urlencode

SEARCH_ENGINES = {
    "duckduckgo": ("DuckDuckGo", "https://duckduckgo.com/"),
    "google": ("Google", "https://www.google.com/search"),
    "bing": ("Bing", "https://www.bing.com/search"),
}


# Accept ASR sentence boundaries between the command words without eating
# meaningful query characters such as the leading dot in .NET.
_SEPARATOR = r"(?:\s|[.,;:!?…](?=\s|$))*"
_SEARCH = re.compile(
    r"\s*search\b" + _SEPARATOR + r"(?:(?:for|up)\b" + _SEPARATOR + r")?(.*)", re.I | re.S
)


def clean_search_query(text):
    # Sentence-final punctuation is commonly supplied by the recognizer.
    # Preserve internal punctuation, decimals, domains, .NET, C++ and C#.
    query = text.strip().rstrip(".,;:!?…").rstrip()
    return (
        query
        if 0 < len(query) <= 1000 and query.isprintable() and any(c.isalnum() for c in query)
        else None
    )


def search_prompt(text):
    match = _SEARCH.fullmatch(text)
    return bool(match and not match[1].strip().strip(".,;:!?…").strip())


def search_query(text):
    match = _SEARCH.fullmatch(text)
    return clean_search_query(match[1]) if match else None


def search_url(engine, query):
    if engine not in SEARCH_ENGINES:
        raise ValueError("Choose a supported search engine.")
    if (
        not isinstance(query, str)
        or not 0 < len(query.strip()) <= 1000
        or not query.isprintable()
    ):
        raise ValueError("Say a search phrase of up to 1000 printable characters.")
    return SEARCH_ENGINES[engine][1] + "?" + urlencode({"q": query.strip()})
