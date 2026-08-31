"""Contract for app/memory/extractor.py::extract_memories — a pure
function, so these tests run it directly with no mocking."""
import unittest

from app.memory.extractor import extract_memories


class ExtractMemoriesTests(unittest.TestCase):
    def test_extracts_preference_from_prefiro_phrase(self):
        result = extract_memories("Eu prefiro receber notificações por e-mail")

        self.assertEqual(len(result["preferences"]), 1)
        self.assertIn("prefiro receber notificações por e-mail", result["preferences"][0])
        self.assertEqual(result["facts"], [])

    def test_extracts_fact_from_sou_phrase(self):
        result = extract_memories("Eu sou gerente de loja há 5 anos")

        self.assertEqual(len(result["facts"]), 1)
        self.assertIn("sou gerente de loja", result["facts"][0])
        self.assertEqual(result["preferences"], [])

    def test_extracts_fact_from_trabalho_na_phrase(self):
        result = extract_memories("Trabalho na filial do centro")

        self.assertEqual(len(result["facts"]), 1)
        self.assertIn("trabalho na filial do centro", result["facts"][0].lower())

    def test_extracts_fact_from_minha_loja_e_phrase(self):
        result = extract_memories("Minha loja é a de maior movimento da região")

        self.assertEqual(len(result["facts"]), 1)

    def test_returns_empty_lists_when_no_pattern_matches(self):
        result = extract_memories("Qual o estoque de leite hoje?")

        self.assertEqual(result, {"preferences": [], "facts": []})

    def test_ignores_message_over_1000_chars(self):
        result = extract_memories("Eu prefiro " + "x" * 1000)

        self.assertEqual(result, {"preferences": [], "facts": []})

    def test_rejects_message_containing_cpf(self):
        result = extract_memories("Eu sou o titular do CPF 123.456.789-00")

        self.assertEqual(result, {"preferences": [], "facts": []})

    def test_rejects_message_containing_cnpj(self):
        result = extract_memories("Eu trabalho na empresa CNPJ 12.345.678/0001-99")

        self.assertEqual(result, {"preferences": [], "facts": []})

    def test_rejects_message_containing_email(self):
        result = extract_memories("Eu prefiro contato via joao@example.com")

        self.assertEqual(result, {"preferences": [], "facts": []})

    def test_rejects_message_containing_password_like_phrase(self):
        result = extract_memories("Eu sou a senha 12345 do sistema antigo")

        self.assertEqual(result, {"preferences": [], "facts": []})

    def test_truncates_extracted_value_to_max_length(self):
        result = extract_memories("Eu prefiro " + "a" * 500)

        self.assertEqual(len(result["preferences"][0]), 200)

    def test_normalizes_internal_whitespace_before_matching(self):
        result = extract_memories("Eu   prefiro   \n receber   ligações")

        self.assertEqual(len(result["preferences"]), 1)
        self.assertNotIn("  ", result["preferences"][0])

    def test_empty_string_returns_empty_lists(self):
        result = extract_memories("")

        self.assertEqual(result, {"preferences": [], "facts": []})


if __name__ == "__main__":
    unittest.main()
