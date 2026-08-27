import unittest

from langchain_openai import ChatOpenAI

from app.agents import runtime
from config.settings import Settings


class LlmRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.original_settings = runtime.settings

    def tearDown(self):
        runtime.settings = self.original_settings

    def test_builds_ollama_cloud_adapter(self):
        runtime.settings = Settings(
            _env_file=None,
            llm_provider="ollama",
            ollama_api_key="test-key",
        )

        llm = runtime.get_llm(temperature=0.2).bound

        self.assertIsInstance(llm, ChatOpenAI)
        self.assertEqual(llm.model_name, "gpt-oss:20b")

    def test_builds_ollama_local_adapter_without_api_key(self):
        runtime.settings = Settings(
            _env_file=None,
            llm_provider="ollama_local",
            ollama_local_base_url="http://127.0.0.1:11434/v1",
            ollama_local_model="qwen2.5:7b-instruct",
        )

        llm = runtime.get_llm(temperature=0.2).bound

        self.assertIsInstance(llm, ChatOpenAI)
        self.assertEqual(llm.model_name, "qwen2.5:7b-instruct")
        self.assertEqual(str(llm.openai_api_base), "http://127.0.0.1:11434/v1")
        self.assertEqual(runtime.get_llm_model_label(), "ollama-local/qwen2.5:7b-instruct")

    def test_rejects_unknown_llm_provider(self):
        with self.assertRaises(ValueError):
            Settings(_env_file=None, llm_provider="unknown")
