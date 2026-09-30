import unittest
from pathlib import Path

from scripts.ci.check_pr import checked, template_errors


class ChecklistTests(unittest.TestCase):
    def test_checked_item(self):
        self.assertTrue(checked("- [x] Respostas validadas", "Respostas validadas"))
        self.assertTrue(checked("* [X] Respostas validadas\r\n", "Respostas validadas"))

    def test_unchecked_or_missing(self):
        for body in ("", "- [ ] Respostas validadas", "- [x] Respostas validadas talvez"):
            self.assertFalse(checked(body, "Respostas validadas"))

    def test_repository_template_is_valid(self):
        template = Path(".github/pull_request_template.md").read_text(encoding="utf-8")
        self.assertEqual(template_errors(template), [])

    def test_missing_template_section_or_checkbox_fails(self):
        template = Path(".github/pull_request_template.md").read_text(encoding="utf-8")
        self.assertTrue(template_errors(template.replace("## Validation", "## Checks")))
        self.assertTrue(template_errors(template.replace("- [ ] Ready for review", "")))

    def test_hidden_or_example_is_not_evidence(self):
        for body in (
            "<!--\n- [x] Respostas validadas\n-->",
            "```md\n- [x] Respostas validadas\n```",
            "~~~\n- [x] Respostas validadas\n~~~",
            "> - [x] Respostas validadas",
            "    - [x] Respostas validadas",
        ):
            self.assertFalse(checked(body, "Respostas validadas"))
