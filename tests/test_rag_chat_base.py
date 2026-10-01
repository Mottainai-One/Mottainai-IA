"""Tests for the shared RAG-chat plumbing used by Cliente and FAQ, and that
each agent wires its own prompt/temperature/history window into it."""
import unittest
from unittest.mock import AsyncMock, patch

from app.agents.rag_chat_base import run_rag_chat_agent


class _StubResponse:
    def __init__(self, content: str):
        self.content = content
        self.usage_metadata = {"input_tokens": 3, "output_tokens": 5}


class _StubLlm:
    def __init__(self):
        self.last_messages = None

    async def ainvoke(self, messages):
        self.last_messages = messages
        return _StubResponse("resposta gerada")


class RunRagChatAgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_builds_messages_with_prompt_memory_context_and_windowed_history(self):
        llm = _StubLlm()
        state = {
            "sanitized_input": "qual a promoção de hoje?",
            "empresa_id": 42,
            "memory": {"facts": ["cliente vip"]},
            "history": [f"msg-{i}" for i in range(15)],
        }

        with (
            patch("app.agents.rag_chat_base.retrieve_with_sources", new=AsyncMock(return_value=("contexto RAG", [{"type": "rag"}]))),
            patch("app.agents.rag_chat_base.format_memory_for_prompt", return_value="MEMORIA_FORMATADA"),
            patch("app.agents.rag_chat_base.get_llm", return_value=llm) as get_llm,
        ):
            result = await run_rag_chat_agent(
                state, system_prompt="PROMPT_BASE", temperature=0.6, history_window=10,
            )

        get_llm.assert_called_once_with(temperature=0.6)
        system_text = str(llm.last_messages[0].content)
        self.assertIn("PROMPT_BASE", system_text)
        self.assertIn("MEMORIA_FORMATADA", system_text)
        self.assertIn("contexto RAG", system_text)
        # window=10 -> last 10 of 15 history items, then the new HumanMessage
        self.assertEqual(llm.last_messages[1:-1], [f"msg-{i}" for i in range(5, 15)])
        self.assertEqual(llm.last_messages[-1].content, "qual a promoção de hoje?")
        self.assertEqual(result["agent_response"], "resposta gerada")
        self.assertEqual(result["sources"], [{"type": "rag"}])
        self.assertEqual(result["input_tokens"], 3)
        self.assertEqual(result["output_tokens"], 5)

    async def test_preserves_the_rest_of_the_state(self):
        state = {
            "sanitized_input": "oi", "empresa_id": 1, "memory": {}, "history": [],
            "user_role": "CLIENTE", "session_id": "s1",
        }
        with (
            patch("app.agents.rag_chat_base.retrieve_with_sources", new=AsyncMock(return_value=("", []))),
            patch("app.agents.rag_chat_base.format_memory_for_prompt", return_value=""),
            patch("app.agents.rag_chat_base.get_llm", return_value=_StubLlm()),
        ):
            result = await run_rag_chat_agent(state, system_prompt="P", temperature=0.1, history_window=5)

        self.assertEqual(result["user_role"], "CLIENTE")
        self.assertEqual(result["session_id"], "s1")


class AgentWiringTests(unittest.IsolatedAsyncioTestCase):
    async def test_cliente_delegates_with_its_own_tuning(self):
        from app.agents import cliente

        delegate = AsyncMock(return_value={"agent_response": "ok"})
        with patch("app.agents.cliente.run_rag_chat_agent", new=delegate):
            await cliente.node_agente_cliente({"sanitized_input": "oi"})

        delegate.assert_awaited_once_with(
            {"sanitized_input": "oi"},
            system_prompt=cliente.SYSTEM_PROMPT,
            temperature=0.6,
            history_window=10,
        )

    async def test_faq_delegates_with_its_own_tuning(self):
        from app.agents import faq

        delegate = AsyncMock(return_value={"agent_response": "ok"})
        with patch("app.agents.faq.run_rag_chat_agent", new=delegate):
            await faq.node_agente_faq({"sanitized_input": "oi"})

        delegate.assert_awaited_once_with(
            {"sanitized_input": "oi"},
            system_prompt=faq.SYSTEM_PROMPT,
            temperature=0.2,
            history_window=8,
        )


if __name__ == "__main__":
    unittest.main()
