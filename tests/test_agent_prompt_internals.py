"""Guards the system prompt each chat agent actually sends against naming the
project's internal systems.

Every chat agent's SYSTEM_PROMPT forbids the assistant from naming internal
systems, databases or technologies to the user. Funcionario and Dono then broke
their own rule by labelling their context blocks "--- Dados operacionais
(PostgreSQL) ---" and "--- Base de conhecimento (RAG) ---", and by citing
"PostgreSQL" as the source of truth in the rule list - handing the model, as a
legitimate name for content, the exact words the prompt told it never to say.

That is the same defect the "[Fonte N - score X]" chunk label caused (see
tests/test_rag_retriever_context.py): the model quotes the labels we give it.
These tests assert the labels are gone from the built message, so a future edit
cannot quietly put a system name back where the model reads it as content.

Mentions inside the prohibition rule itself are deliberate - a negative example
("NUNCA mencionar ... ex: 'PostgreSQL'") teaches the ban rather than offering a
label - so the assertions target the parenthesised-label form and the rule
lines, not the bare word.
"""
import unittest
from unittest.mock import AsyncMock, patch

# Internal names presented as a label for content, which is the form the model
# reads as "this is what this block is called".
FORBIDDEN_LABELS = ("(PostgreSQL)", "(RAG)", "(Postgres)")


class _StubResponse:
    content = "resposta"
    usage_metadata = {"input_tokens": 1, "output_tokens": 1}


def _base_state():
    return {
        "sanitized_input": "oi", "empresa_id": 1, "usuario_id": 9, "session_id": "s1",
        "memory": {"preferences": [], "facts": [], "lastAgent": None, "lastSkill": None},
        "history": [],
    }


class FuncionarioPromptTests(unittest.IsolatedAsyncioTestCase):
    async def _system_prompt(self):
        from app.agents import funcionario

        captured = {}

        async def _capture(messages):
            captured["messages"] = messages
            return _StubResponse()

        with (
            patch("app.agents.funcionario.get_stock_alerts", new=AsyncMock(return_value=[])),
            patch("app.agents.funcionario.get_inventory_status", new=AsyncMock(return_value=[])),
            patch("app.agents.funcionario.get_expiring_batches", new=AsyncMock(return_value=[])),
            patch("app.agents.funcionario.get_inbox", new=AsyncMock(return_value=[])),
            patch("app.agents.funcionario.format_notifications_for_agent", new=AsyncMock(return_value="")),
            patch("app.agents.funcionario.retrieve_with_sources", new=AsyncMock(return_value=("", []))),
            patch("app.agents.funcionario.get_recent_vision_analyses", new=AsyncMock(return_value=[])),
            patch("app.agents.funcionario.get_llm") as get_llm,
        ):
            get_llm.return_value.ainvoke = _capture
            await funcionario.node_agente_funcionario(_base_state())

        return str(captured["messages"][0].content)

    async def test_context_blocks_are_not_labelled_with_internal_system_names(self):
        system_text = await self._system_prompt()

        for label in FORBIDDEN_LABELS:
            self.assertNotIn(label, system_text)

    async def test_source_of_truth_rule_names_the_data_not_the_database(self):
        system_text = await self._system_prompt()

        self.assertIn("- Usar os dados operacionais fornecidos como fonte da verdade.", system_text)
        self.assertNotIn("dados do PostgreSQL", system_text)

    async def test_context_blocks_are_still_labelled_and_distinguishable(self):
        # Removing the system names must not remove the headings themselves:
        # without them the operational data and the manuals run together.
        system_text = await self._system_prompt()

        self.assertIn("--- Dados operacionais ---", system_text)
        self.assertIn("--- Base de conhecimento ---", system_text)
        self.assertIn("--- Memória do usuário ---", system_text)


class DonoPromptTests(unittest.IsolatedAsyncioTestCase):
    async def _system_prompt(self):
        from app.agents import dono

        captured = {}

        async def _capture(messages):
            captured["messages"] = messages
            return _StubResponse()

        with (
            patch("app.agents.dono.get_kpis", new=AsyncMock(return_value={})),
            patch("app.agents.dono.get_sales_summary", new=AsyncMock(return_value=[])),
            patch("app.agents.dono.get_stock_alerts", new=AsyncMock(return_value=[])),
            patch("app.agents.dono.get_kpis_by_store", new=AsyncMock(return_value=[])),
            patch("app.agents.dono.retrieve_with_sources", new=AsyncMock(return_value=("", []))),
            patch("app.agents.dono.get_llm") as get_llm,
        ):
            get_llm.return_value.ainvoke = _capture
            await dono.node_agente_dono(_base_state())

        return str(captured["messages"][0].content)

    async def test_context_blocks_are_not_labelled_with_internal_system_names(self):
        system_text = await self._system_prompt()

        for label in FORBIDDEN_LABELS:
            self.assertNotIn(label, system_text)

    async def test_factual_basis_rule_names_the_data_not_the_database(self):
        system_text = await self._system_prompt()

        self.assertIn("- Usar os dados analíticos fornecidos como base factual", system_text)
        self.assertNotIn("dados do PostgreSQL", system_text)

    async def test_context_blocks_are_still_labelled_and_distinguishable(self):
        system_text = await self._system_prompt()

        self.assertIn("--- Dados analíticos ---", system_text)
        self.assertIn("--- Base de conhecimento ---", system_text)
        self.assertIn("--- Memória do usuário ---", system_text)


class ClienteAndFaqPromptTests(unittest.IsolatedAsyncioTestCase):
    async def test_shared_rag_chat_prompt_names_no_internal_system(self):
        # Cliente/FAQ already used a neutral heading; pinned so the two paths
        # cannot drift back apart.
        from app.agents.rag_chat_base import run_rag_chat_agent

        captured = {}

        async def _capture(messages):
            captured["messages"] = messages
            return _StubResponse()

        with (
            patch("app.agents.rag_chat_base.retrieve_with_sources", new=AsyncMock(return_value=("", []))),
            patch("app.agents.rag_chat_base.get_llm") as get_llm,
        ):
            get_llm.return_value.ainvoke = _capture
            await run_rag_chat_agent(
                _base_state(), system_prompt="PROMPT", temperature=0.6, history_window=10,
            )

        system_text = str(captured["messages"][0].content)
        for label in FORBIDDEN_LABELS:
            self.assertNotIn(label, system_text)
        self.assertIn("--- Informações disponíveis ---", system_text)


class FuncionarioContextSizeTests(unittest.IsolatedAsyncioTestCase):
    """The operational context must stay bounded.

    Every block Funcionario packs is capped except the expiring batches, which
    were serialised whole. With 20 batches near expiry that block alone reached
    ~1.4k tokens and pushed the request to 8120 against the provider's 8000 TPM
    ceiling, so the agent returned HTTP 503 to *every* question. The prompt grows
    with the data, so this is a cap that has to stay.
    """

    async def _system_prompt(self, expiring):
        from app.agents import funcionario

        captured = {}

        async def _capture(messages):
            captured["messages"] = messages
            return _StubResponse()

        with (
            patch("app.agents.funcionario.get_stock_alerts", new=AsyncMock(return_value=[])),
            patch("app.agents.funcionario.get_inventory_status", new=AsyncMock(return_value=[])),
            patch("app.agents.funcionario.get_expiring_batches", new=AsyncMock(return_value=expiring)),
            patch("app.agents.funcionario.get_inbox", new=AsyncMock(return_value=[])),
            patch("app.agents.funcionario.format_notifications_for_agent", new=AsyncMock(return_value="")),
            patch("app.agents.funcionario.retrieve_with_sources", new=AsyncMock(return_value=("", []))),
            patch("app.agents.funcionario.get_recent_vision_analyses", new=AsyncMock(return_value=[])),
            patch("app.agents.funcionario.get_llm") as get_llm,
        ):
            get_llm.return_value.ainvoke = _capture
            await funcionario.node_agente_funcionario(_base_state())

        return str(captured["messages"][0].content)

    @staticmethod
    def _batches(n):
        return [{"batch_id": i, "batch_code": f"L{i:04d}", "days_to_expire": i} for i in range(n)]

    async def test_only_the_most_urgent_batches_are_serialised(self):
        from app.agents.funcionario import EXPIRING_BATCHES_IN_PROMPT

        system_text = await self._system_prompt(self._batches(40))

        # get_expiring_batches() orders by expiration_date ASC, so the cap must
        # keep the head of the list - the batches actually about to expire.
        self.assertIn('"batch_code": "L0000"', system_text)
        self.assertIn(f'"batch_code": "L{EXPIRING_BATCHES_IN_PROMPT - 1:04d}"', system_text)
        self.assertNotIn(f'"batch_code": "L{EXPIRING_BATCHES_IN_PROMPT:04d}"', system_text)

    async def test_heading_reports_the_true_total_not_the_slice(self):
        # Without the real count the agent would present the slice as the whole
        # picture - "you have 10 batches expiring" when there are 40.
        system_text = await self._system_prompt(self._batches(40))

        self.assertIn("LOTES VENCENDO EM 7 DIAS (total 40, listando os 10 mais urgentes):", system_text)

    async def test_heading_is_accurate_when_there_is_nothing_to_truncate(self):
        system_text = await self._system_prompt(self._batches(3))

        self.assertIn("LOTES VENCENDO EM 7 DIAS (total 3, listando os 3 mais urgentes):", system_text)


if __name__ == "__main__":
    unittest.main()
