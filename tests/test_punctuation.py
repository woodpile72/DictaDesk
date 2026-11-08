# SPDX-License-Identifier: GPL-3.0-only
import unittest
from dictadesk.core.transcript import Transcript, punctuate, provisional
from dictadesk.core.settings import Preferences


class PunctuationTests(unittest.TestCase):
    def test_provisional_ending_keeps_spoken_commands_and_internal_punctuation(self):
        self.assertEqual(provisional("Hello.", "Hello."), "Hello")
        self.assertEqual(provisional('He said "hello."', 'He said "hello."'), 'He said "hello')
        self.assertEqual(
            provisional("Hello. Next thought.", "Hello. Next thought."), "Hello. Next thought"
        )
        for command in ("period", "question mark", "dot dot dot", "new paragraph"):
            raw = "Hello " + command
            self.assertEqual(provisional(punctuate(raw), raw, True), punctuate(raw))

    def test_requested_sentence(self):
        self.assertEqual(
            punctuate("This is just a test period recording with dictadesk period."),
            "This is just a test. Recording with dictadesk.",
        )

    def test_ellipsis_and_asr_inserted_punctuation(self):
        for text in ("Wait dot dot dot", "Wait, dot, dot, dot.", "Wait DOT DOT DOT."):
            with self.subTest(text=text):
                self.assertEqual(punctuate(text), "Wait…")
        self.assertEqual(punctuate("Wait dot dot dot maybe later."), "Wait… maybe later.")

    def test_marks_spacing_and_sentence_capitalization(self):
        self.assertEqual(
            punctuate(
                "Hello comma world exclamation point are you ready question mark yes period"
            ),
            "Hello, world! Are you ready? Yes.",
        )

    def test_all_delimiter_aliases(self):
        for name, start, end in (
            ("parenthesis", "(", ")"),
            ("bracket", "[", "]"),
            ("brace", "{", "}"),
        ):
            for opening in ("left", "open"):
                for closing in ("right", "close"):
                    with self.subTest(name=name, opening=opening, closing=closing):
                        self.assertEqual(
                            punctuate(f"Note {opening} {name} text {closing} {name}."),
                            f"Note {start}text{end}.",
                        )

    def test_delimiter_spacing_nested_empty_and_following_punctuation(self):
        cases = {
            "open parenthesis hello close parenthesis world": "(hello) world",
            "open bracket open brace item close brace close bracket period": "[{item}].",
            "open parenthesis close parenthesis": "()",
            "word open parenthesis note exclamation point close parenthesis comma next": "word (note!), next",
            "word open parenthesis note! close parenthesis.": "word (note!).",
            "open parenthesis new line text close parenthesis": "(\nText)",
        }
        for original, expected in cases.items():
            with self.subTest(original=original):
                self.assertEqual(punctuate(original), expected)
        self.assertEqual(
            punctuate("Choices colon one semicolon two full stop"), "Choices: one; two."
        )

    def test_newlines_preserve_previous_explicit_punctuation(self):
        self.assertEqual(
            punctuate("Hello period new paragraph next new line item period"),
            "Hello.\n\nNext\nItem.",
        )

    def test_no_commands_means_no_changes(self):
        for text in (
            "A periodic table; commandment.",
            "https://example.com/period",
            "name@period.com",
            "Hello… World!",
            "Newline is a word.",
        ):
            with self.subTest(text=text):
                self.assertEqual(punctuate(text), text)

    def test_opt_in_and_undo_restore_exact_original(self):
        result = Transcript()
        original = "eric says dot dot dot."
        result.replace(original, [])
        self.assertEqual(result.text, original)
        result.replace(original, [["eric", "Erick"]], spoken_punctuation=True)
        self.assertEqual(result.text, "Erick says…")
        result.undo = True
        self.assertEqual(result.text, original)
        result.undo = False
        self.assertEqual(result.text, "Erick says…")

    def test_vocabulary_cannot_inject_new_commands(self):
        result = Transcript()
        result.replace("era", [["era", "period"]], spoken_punctuation=True)
        self.assertEqual(result.text, "period")

    def test_preference_requires_boolean(self):
        self.assertFalse(Preferences().spoken_punctuation)
        with self.assertRaises(ValueError):
            Preferences(spoken_punctuation="yes").validate()


class TranscriptContentTests(unittest.TestCase):
    def test_empty_results_are_rejected_but_spoken_formatting_is_retained(self):
        from dictadesk.core.transcript import has_content

        for value in (None, [], "", "   "):
            self.assertFalse(has_content(value))
        for value in ("text", "\n", "\n\n", "\t"):
            self.assertTrue(has_content(value))
