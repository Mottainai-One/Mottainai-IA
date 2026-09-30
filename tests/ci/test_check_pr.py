import unittest

from scripts.ci.check_pr import checked


class ChecklistTests(unittest.TestCase):
    def test_checked_item(self):
        self.assertTrue(checked("- [x] Respostas validadas", "Respostas validadas"))
        self.assertTrue(checked("* [X] Respostas validadas\r\n", "Respostas validadas"))

    def test_unchecked_or_missing(self):
        for body in ("", "- [ ] Respostas validadas", "- [x] Respostas validadas talvez"):
            self.assertFalse(checked(body, "Respostas validadas"))

    def test_hidden_or_example_is_not_evidence(self):
        for body in (
            "<!--\n- [x] Respostas validadas\n-->",
            "```md\n- [x] Respostas validadas\n```",
            "~~~\n- [x] Respostas validadas\n~~~",
            "> - [x] Respostas validadas",
            "    - [x] Respostas validadas",
        ):
            self.assertFalse(checked(body, "Respostas validadas"))
