# SPDX-License-Identifier: GPL-3.0-only
import unittest
from dataclasses import replace

from dictadesk.core.commands import REGISTRY, recognize
from dictadesk.core.command_matching import suggest_commands
from dictadesk.core.settings import Preferences
from dictadesk.core.voice_commands import choice_number


class MatchingTests(unittest.TestCase):
    def test_small_spelling_errors_offer_valid_commands(self):
        for heard, expected in (
            ("volum up", ("volume", ("up", 5))),
            ("brighness down 15 percent", ("brightness", ("down", 15))),
            ("lock screan", ("lock", ())),
            ("suspent computer", ("suspend", ())),
            ("set a timmer for fifteen minutes", ("timer", ("set", 900))),
            ("launc Firefox", ("open", ("firefox",))),
        ):
            with self.subTest(heard=heard):
                matches = suggest_commands(heard)
                self.assertTrue(matches)
                self.assertEqual((matches[0].kind, matches[0].arguments), expected)
                self.assertEqual(recognize(heard)[0], "unknown")

    def test_existing_commands_never_go_through_fuzzy_path(self):
        for spec in REGISTRY:
            for phrase in spec.phrases:
                if recognize(phrase)[0] != "unknown":
                    self.assertEqual(suggest_commands(phrase), [], phrase)
        self.assertEqual(suggest_commands("screenshaver"), [])

    def test_numbers_and_opposing_words_are_not_corrected(self):
        for text in (
            "volume dowm",
            "volume unmte",
            "volum uo",
            "volume fivety",
            "volum fifteenx",
            "volum -15",
            "volum +15",
            "volum 1.5",
            "volum 150",
            "set a timmer for -5 minutes",
            "set a timmer for 1.5 hours",
            "set a timmer for fiftyx minutes",
            "not lock screan",
            "do not lock screan",
            "don't lock screan",
            "never suspent computer",
            "volume up and lock screan",
            "lock screan and close window",
            "please lock screan",
            "this is ordinary speech about a timmer",
            "a" * 1001,
        ):
            self.assertEqual(suggest_commands(text), [], text)
        for number in ("fifteen", "fifty", "15", "50"):
            suggestion = suggest_commands("volum " + number)[0]
            self.assertEqual(suggestion.arguments, recognize("volume " + number)[1])

    def test_argument_text_and_preferences_are_preserved(self):
        item = suggest_commands("serch for C++ .NET Version 3.5")[0]
        self.assertEqual(item.arguments, ("C++ .NET Version 3.5",))
        self.assertEqual(suggest_commands("search dewwalt impact"), [])
        item = suggest_commands("volum up", replace(Preferences(), volume_step=12))[0]
        self.assertEqual(item.arguments, ("up", 12))

    def test_aliases_deduplicate_by_action(self):
        result = suggest_commands("set a timmer for 10 minutes")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].arguments, ("set", 600))

    def test_yes_no_are_only_for_single_choice_confirmation(self):
        for phrase in ("yes", "Yes!", "yes please", "confirm"):
            self.assertIsNone(choice_number(phrase))
            self.assertEqual(choice_number(phrase, confirmation=True), 1)
        for phrase in ("no", "No thanks."):
            self.assertEqual(choice_number(phrase, confirmation=True), "cancel")
        for phrase in ("yes and open Firefox", "no yes", "maybe", "yesterday"):
            self.assertIsNone(choice_number(phrase, confirmation=True))


class CueTests(unittest.TestCase):
    def test_ready_and_acceptance_have_distinct_pulse_counts(self):
        from dictadesk.audio.handsfree import cue_audio
        from array import array

        rate, single = cue_audio()
        _, double = cue_audio(accepted=True)
        self.assertEqual(rate, 16000)
        self.assertEqual(len(single), 1920 * 2)
        self.assertEqual(len(double), 1280 * 4 + 1920)
        self.assertEqual(double[2560:4480], bytes(1920))
        self.assertEqual(double[:2560], double[4480:])
        self.assertGreater(max(array("h", single)), 3000)
