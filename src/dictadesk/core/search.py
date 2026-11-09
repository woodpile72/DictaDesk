# SPDX-License-Identifier: GPL-3.0-only
"""Explicit search providers and encoded query URLs; never interpret query text."""

import re
from urllib.parse import urlencode

SEARCH_ENGINES = {
    "duckduckgo": ("DuckDuckGo", "https://duckduckgo.com/"),
    "google": ("Google", "https://www.google.com/search"),
    "bing": ("Bing", "https://www.bing.com/search"),
}


def search_query(text):
    match = re.fullmatch(r"\s*search\b(?:\s+(?:for|up)\b)?(.*)", text, re.I | re.S)
    query = match[1].strip() if match else ""
    return (
        query
        if 0 < len(query) <= 1000 and query.isprintable() and any(c.isalnum() for c in query)
        else None
    )


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
