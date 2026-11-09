# SPDX-License-Identifier: GPL-3.0-only
"""Search grammar, settings compatibility and browser dispatch without networking."""

from dataclasses import asdict
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from dictadesk.core.search import SEARCH_ENGINES, search_url, search_prompt
from dictadesk.core.settings import Preferences, SettingsStore
from dictadesk.core.voice_commands import command, wake_activation
from dictadesk.desktop.search import launch
from dictadesk.ui.gtk import GLib


class SearchTests(unittest.TestCase):
    def test_all_prefixes_in_both_wake_forms(self):
        for prefix in ("search", "search for", "search up"):
            text = prefix + " Linux Mint"
            self.assertEqual(command(text), ("search", ("Linux Mint",)))
            kind, suffix = wake_activation("Computer " + text, "Computer")
            self.assertEqual(kind, "exact")
            self.assertEqual(command(suffix), ("search", ("linux mint",)))
        for text in (
            "search",
            "search for",
            "search up",
            "search for ",
            "search.",
            "search for.",
            "please search cats",
            "research cats",
            "search " + "x" * 1001,
        ):
            self.assertEqual(command(text), ("unknown", ()), text)

    def test_asr_sentence_punctuation_is_removed_without_damaging_query_syntax(self):
        for text, expected in (
            ("Search Linux Mint.", "Linux Mint"),
            ("Search for Linux Mint?!", "Linux Mint"),
            ("Search. For Linux Mint.", "Linux Mint"),
            ("Search up .NET 8.0.", ".NET 8.0"),
            ("Search C++.", "C++"),
            ("Search C#.", "C#"),
            ("Search example.com/path?x=1&y=2.", "example.com/path?x=1&y=2"),
        ):
            self.assertEqual(command(text), ("search", (expected,)), text)
        for text in ("Search.", "Search for.", "Search up!", "Search. For."):
            self.assertTrue(search_prompt(text), text)
        for text in ("research", "search linux mint.", "search " + "x" * 1001):
            self.assertFalse(search_prompt(text), text)

    def test_query_is_data_and_only_one_action(self):
        query = "C++ & C# café? q=1 #tag $(touch nope); lock screen"
        self.assertEqual(command("search for " + query), ("search", (query,)))
        self.assertEqual(command("search fortran"), ("search", ("fortran",)))
        for engine, (_name, base) in SEARCH_ENGINES.items():
            parts = urlsplit(search_url(engine, query))
            self.assertEqual(parts.scheme, "https")
            self.assertEqual(parts.netloc, urlsplit(base).netloc)
            self.assertEqual(parts.fragment, "")
            self.assertEqual(parse_qs(parts.query), {"q": [query]})
        with self.assertRaises(ValueError):
            search_url("unsupported", "cats")

    def test_old_settings_default_to_ddg_and_selection_persists(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "preferences.json"
            values = asdict(Preferences())
            values.pop("search_engine")
            path.write_text(json.dumps({"schema": 1, "preferences": values}))
            store = SettingsStore(path)
            prefs = store.read()
            self.assertEqual(store.problem, "")
            self.assertEqual(prefs.search_engine, "duckduckgo")
            prefs.search_engine = "bing"
            store.save(prefs)
            self.assertEqual(store.read().search_engine, "bing")
        with self.assertRaises(ValueError):
            Preferences(search_engine="invalid").validate()

    def test_browser_dispatch_and_stale_callbacks(self):
        valid = Mock(return_value=True)
        done = Mock()
        with (
            patch("dictadesk.desktop.search.Gio.AppInfo.launch_default_for_uri_async") as start,
            patch(
                "dictadesk.desktop.search.Gio.AppInfo.launch_default_for_uri_finish",
                return_value=True,
            ),
        ):
            launch("cats & dogs", "duckduckgo", valid, done)
            self.assertEqual(start.call_args.args[0], "https://duckduckgo.com/?q=cats+%26+dogs")
            callback = start.call_args.args[3]
            callback(None, Mock())
            done.assert_called_once_with("")
            done.reset_mock()
            valid.return_value = False
            callback(None, Mock())
            done.assert_not_called()
            start.reset_mock()
            launch("cats", "google", valid, done)
            start.assert_not_called()

    def test_browser_failure_is_reported_without_query(self):
        done = Mock()
        with patch(
            "dictadesk.desktop.search.Gio.AppInfo.launch_default_for_uri_async",
            side_effect=GLib.Error("fixture"),
        ):
            launch("private query", "bing", lambda: True, done)
        self.assertEqual(done.call_count, 1)
        self.assertNotIn("private query", done.call_args.args[0])
