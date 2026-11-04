# SPDX-License-Identifier: GPL-3.0-only
"""Verify the small evaluation harness's scoring, without any model downloads."""

import unittest

from model_evaluation import distance


class EvaluationScoringTests(unittest.TestCase):
    def test_case_and_punctuation_are_ignored(self):
        self.assertEqual(distance("Hello, world!", "hello world")["wer"], 0)

    def test_insertions_deletions_and_substitutions(self):
        self.assertEqual(distance("one two three", "one extra two three")["word_errors"], 1)
        self.assertEqual(distance("one two three", "one three")["word_errors"], 1)
        self.assertEqual(distance("one two three", "one four three")["word_errors"], 1)

    def test_silence_does_not_create_a_fake_accuracy_percentage(self):
        self.assertIsNone(distance("", "Thanks for watching")["wer"])
        self.assertEqual(distance("", "Thanks for watching")["word_errors"], 3)

    def test_spoken_commands_remain_words_in_raw_recognition_score(self):
        self.assertEqual(distance("hello comma world period", "hello world")["word_errors"], 2)
