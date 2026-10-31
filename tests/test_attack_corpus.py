"""Regression suite: every known attack string must stay blocked or neutralised."""
import json
import pathlib
import unittest

from hormuz import guardrails

CORPUS = json.loads((pathlib.Path(__file__).parent / "data" / "attack_corpus.json").read_text())


class AttackCorpusTests(unittest.TestCase):
    def test_blocked_inputs(self):
        for text in CORPUS["blocked_inputs"]:
            with self.subTest(text=text):
                self.assertIsNotNone(guardrails.check_input(text))

    def test_allowed_inputs(self):
        for text in CORPUS["allowed_inputs"]:
            with self.subTest(text=text):
                self.assertIsNone(guardrails.check_input(text))

    def test_research_injections_removed(self):
        for text in CORPUS["research_injections"]:
            with self.subTest(text=text):
                cleaned = guardrails.sanitize_research("news", text)
                self.assertIn("[removed]", cleaned)

    def test_secrets_redacted(self):
        for text in CORPUS["secrets"]:
            with self.subTest(text=text):
                self.assertIn("[REDACTED]", guardrails.redact_secrets(text))


if __name__ == "__main__":
    unittest.main()
