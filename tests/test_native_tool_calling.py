"""Native tool calling (PR: feat/native-tool-calling): app/agents/
tools_bridge.py's per-request toolkit and app/agents/runtime.
run_agent_with_tools' tool-calling loop.
"""
import unittest
from unittest.mock import AsyncMock, patch

from langchain_core.messages import AIMessage, HumanMessage

from app.agents.runtime import run_agent_with_tools
from app.agents.tools_bridge import build_toolkit


def _tool_call(name, args, call_id="call-1"):
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


class FakeChatModel:
    """Stands in for the object app.agents.runtime._build_llm() returns.
    bind_tools/with_retry both return self (matching how the real
    RunnableBinding chain behaves for this test's purposes), so a single
    response queue drives every ainvoke() the loop makes, tool-bound or
    not."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.bound_tools = None
        self.ainvoke_calls = 0

    def bind_tools(self, tools):
        self.bound_tools = tools
        return self

    def with_retry(self, **kwargs):
        return self

    async def ainvoke(self, messages):
        self.ainvoke_calls += 1
        return self._responses.pop(0)


class BuildToolkitTenantSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_model_cannot_smuggle_a_different_empresa_id(self):
        """The tool's schema has no empresa_id field at all, so it's not
        merely rejected — it's structurally impossible for it to reach
        the wrapped Postgres call."""
        tool_runs = []
        captured = {}

        async def fake_get_stock_alerts(empresa_id, limit=10, store_id=None):
            captured["empresa_id"] = empresa_id
            return []

        with patch("app.agents.tools_bridge.get_stock_alerts", new=fake_get_stock_alerts):
            [tool] = build_toolkit(
                ["get_stock_alerts"], empresa_id=42, tool_runs=tool_runs, sources_acc=[],
            )
            # A model's tool_call args are just a dict — this simulates one
            # that includes a hallucinated/injected empresa_id.
            await tool.ainvoke({"limit": 3, "empresa_id": 999})

        self.assertEqual(captured["empresa_id"], 42)  # the real one, from the closure — never 999

    async def test_records_the_real_empresa_id_in_tool_runs_regardless(self):
        tool_runs = []
        with patch("app.agents.tools_bridge.get_stock_alerts", new=AsyncMock(return_value=[])):
            [tool] = build_toolkit(
                ["get_stock_alerts"], empresa_id=42, tool_runs=tool_runs, sources_acc=[],
            )
            await tool.ainvoke({"empresa_id": 999})

        self.assertEqual(len(tool_runs), 1)
        self.assertEqual(tool_runs[0]["input"]["empresa_id"], 42)

    async def test_retrieve_with_sources_tool_extends_the_shared_sources_list(self):
        tool_runs, sources = [], []
        fake = AsyncMock(return_value=("contexto relevante", [{"type": "rag", "ref": "doc:1", "score": 0.9}]))
        with patch("app.agents.tools_bridge.retrieve_with_sources", new=fake):
            [tool] = build_toolkit(
                ["retrieve_with_sources"], empresa_id=1, tool_runs=tool_runs, sources_acc=sources,
            )
            content = await tool.ainvoke({"query": "como funciona o descarte?"})

        self.assertEqual(content, "contexto relevante")
        self.assertEqual(sources, [{"type": "rag", "ref": "doc:1", "score": 0.9}])


class BuildToolkitLimitsTests(unittest.TestCase):
    def test_rejects_more_tools_than_the_configured_ceiling(self):
        with patch("app.agents.tools_bridge.get_settings") as get_settings:
            get_settings.return_value.agent_tool_calling_max_tools = 2
            with self.assertRaises(ValueError):
                build_toolkit(
                    ["get_stock_alerts", "get_kpis", "get_sales_summary"],
                    empresa_id=1, tool_runs=[], sources_acc=[],
                )

    def test_accepts_a_toolkit_at_the_ceiling(self):
        with patch("app.agents.tools_bridge.get_settings") as get_settings:
            get_settings.return_value.agent_tool_calling_max_tools = 2
            tools = build_toolkit(
                ["get_stock_alerts", "get_kpis"], empresa_id=1, tool_runs=[], sources_acc=[],
            )
        self.assertEqual(len(tools), 2)


class RunAgentWithToolsTests(unittest.IsolatedAsyncioTestCase):
    async def test_calls_the_tool_the_model_requests_and_returns_its_final_answer(self):
        tool = AsyncMock(return_value="alertas: nenhum")
        tool.name = "get_stock_alerts"
        fake_llm = FakeChatModel([
            AIMessage(content="", tool_calls=[_tool_call("get_stock_alerts", {"limit": 5})]),
            AIMessage(content="Não há alertas ativos."),
        ])

        with patch("app.agents.runtime._build_llm", return_value=fake_llm):
            response = await run_agent_with_tools([tool], [HumanMessage(content="tem alerta?")])

        tool.ainvoke.assert_awaited_once_with({"limit": 5})
        self.assertEqual(response.content, "Não há alertas ativos.")

    async def test_returns_immediately_when_the_model_needs_no_tool(self):
        fake_llm = FakeChatModel([AIMessage(content="Tudo certo por aqui.")])

        with patch("app.agents.runtime._build_llm", return_value=fake_llm):
            response = await run_agent_with_tools([], [HumanMessage(content="oi")])

        self.assertEqual(response.content, "Tudo certo por aqui.")
        self.assertEqual(fake_llm.ainvoke_calls, 1)

    async def test_stops_after_max_iterations_and_forces_a_final_answer(self):
        tool = AsyncMock(return_value="...")
        tool.name = "get_kpis"
        # 3 rounds of "still want a tool" + 1 final content-only answer.
        fake_llm = FakeChatModel([
            AIMessage(content="", tool_calls=[_tool_call("get_kpis", {}, "c1")]),
            AIMessage(content="", tool_calls=[_tool_call("get_kpis", {}, "c2")]),
            AIMessage(content="", tool_calls=[_tool_call("get_kpis", {}, "c3")]),
            AIMessage(content="Resposta final, sem mais chamadas."),
        ])

        with patch("app.agents.runtime._build_llm", return_value=fake_llm):
            response = await run_agent_with_tools(
                [tool], [HumanMessage(content="me dá tudo")], max_iterations=3,
            )

        self.assertEqual(tool.ainvoke.await_count, 3)  # never a 4th round
        self.assertEqual(fake_llm.ainvoke_calls, 4)  # 3 tool-bound + 1 forced final
        self.assertEqual(response.content, "Resposta final, sem mais chamadas.")

    async def test_a_failing_tool_call_is_fed_back_as_content_not_raised(self):
        tool = AsyncMock(side_effect=RuntimeError("postgres down"))
        tool.name = "get_kpis"
        fake_llm = FakeChatModel([
            AIMessage(content="", tool_calls=[_tool_call("get_kpis", {})]),
            AIMessage(content="Não consegui buscar os KPIs agora."),
        ])

        with patch("app.agents.runtime._build_llm", return_value=fake_llm):
            response = await run_agent_with_tools([tool], [HumanMessage(content="kpis?")])

        self.assertEqual(response.content, "Não consegui buscar os KPIs agora.")

    async def test_unknown_tool_name_is_fed_back_as_content_not_raised(self):
        fake_llm = FakeChatModel([
            AIMessage(content="", tool_calls=[_tool_call("nao_existe", {})]),
            AIMessage(content="ok"),
        ])

        with patch("app.agents.runtime._build_llm", return_value=fake_llm):
            response = await run_agent_with_tools([], [HumanMessage(content="oi")])

        self.assertEqual(response.content, "ok")


def _state(role="ESTOQUISTA"):
    return {
        "session_id": "s1", "empresa_id": 1, "usuario_id": 2, "user_role": role,
        "user_input": "oi", "sanitized_input": "oi", "history": [],
        "memory": {"preferences": [], "facts": [], "lastAgent": None, "lastSkill": None},
    }


class FuncionarioNativeDispatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_flag_off_uses_the_legacy_path(self):
        from app.agents import funcionario

        with patch("app.agents.funcionario.get_settings") as get_settings:
            get_settings.return_value.native_tool_calling_enabled = False
            with (
                patch("app.agents.funcionario._node_agente_funcionario_legacy",
                      new=AsyncMock(return_value={"agent_response": "legacy"})) as legacy,
                patch("app.agents.funcionario._node_agente_funcionario_native", new=AsyncMock()) as native,
            ):
                result = await funcionario.node_agente_funcionario(_state())

        legacy.assert_awaited_once()
        native.assert_not_awaited()
        self.assertEqual(result["agent_response"], "legacy")

    async def test_flag_on_uses_the_native_path(self):
        from app.agents import funcionario

        with patch("app.agents.funcionario.get_settings") as get_settings:
            get_settings.return_value.native_tool_calling_enabled = True
            with (
                patch("app.agents.funcionario._node_agente_funcionario_legacy", new=AsyncMock()) as legacy,
                patch("app.agents.funcionario._node_agente_funcionario_native",
                      new=AsyncMock(return_value={"agent_response": "native"})) as native,
            ):
                result = await funcionario.node_agente_funcionario(_state())

        native.assert_awaited_once()
        legacy.assert_not_awaited()
        self.assertEqual(result["agent_response"], "native")


class FuncionarioNativeNodeTests(unittest.IsolatedAsyncioTestCase):
    async def test_builds_the_expected_toolkit_and_returns_the_models_answer(self):
        from app.agents import funcionario

        fake_response = AIMessage(
            content="resposta nativa",
            usage_metadata={"input_tokens": 3, "output_tokens": 4, "total_tokens": 7},
        )

        with (
            patch("app.agents.funcionario.get_inbox", new=AsyncMock(return_value=[])),
            patch("app.agents.funcionario.get_recent_vision_analyses", new=AsyncMock(return_value=[])),
            patch("app.agents.funcionario.format_notifications_for_agent", new=AsyncMock(return_value="")),
            patch("app.agents.funcionario.build_toolkit") as build_toolkit,
            patch("app.agents.funcionario.run_agent_with_tools", new=AsyncMock(return_value=fake_response)) as run_agent,
        ):
            build_toolkit.return_value = []
            result = await funcionario._node_agente_funcionario_native(_state())

        # get_inbox/get_recent_vision_analyses are NOT in the toolkit — they
        # stay always-fetched context, same as the legacy path.
        requested_names = build_toolkit.call_args.args[0]
        self.assertEqual(
            set(requested_names),
            {"get_stock_alerts", "get_inventory_status", "get_expiring_batches", "retrieve_with_sources"},
        )
        run_agent.assert_awaited_once()
        self.assertEqual(result["agent_response"], "resposta nativa")
        self.assertEqual(result["input_tokens"], 3)
        self.assertEqual(result["output_tokens"], 4)
        # get_inbox + get_recent_vision_analyses + format_notifications
        # still recorded in tool_runs even though they bypass the toolkit.
        self.assertEqual(
            {run["tool"] for run in result["tool_runs"]},
            {"get_inbox", "get_recent_vision_analyses", "format_notifications_for_agent"},
        )


class DonoNativeDispatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_flag_off_uses_the_legacy_path(self):
        from app.agents import dono

        with patch("app.agents.dono.get_settings") as get_settings:
            get_settings.return_value.native_tool_calling_enabled = False
            with (
                patch("app.agents.dono._node_agente_dono_legacy",
                      new=AsyncMock(return_value={"agent_response": "legacy"})) as legacy,
                patch("app.agents.dono._node_agente_dono_native", new=AsyncMock()) as native,
            ):
                result = await dono.node_agente_dono(_state("DONO"))

        legacy.assert_awaited_once()
        native.assert_not_awaited()
        self.assertEqual(result["agent_response"], "legacy")

    async def test_flag_on_uses_the_native_path(self):
        from app.agents import dono

        with patch("app.agents.dono.get_settings") as get_settings:
            get_settings.return_value.native_tool_calling_enabled = True
            with (
                patch("app.agents.dono._node_agente_dono_legacy", new=AsyncMock()) as legacy,
                patch("app.agents.dono._node_agente_dono_native",
                      new=AsyncMock(return_value={"agent_response": "native"})) as native,
            ):
                result = await dono.node_agente_dono(_state("DONO"))

        native.assert_awaited_once()
        legacy.assert_not_awaited()
        self.assertEqual(result["agent_response"], "native")


class DonoNativeNodeTests(unittest.IsolatedAsyncioTestCase):
    async def test_builds_the_expected_toolkit_and_returns_the_models_answer(self):
        from app.agents import dono

        fake_response = AIMessage(
            content="resposta nativa",
            usage_metadata={"input_tokens": 5, "output_tokens": 6, "total_tokens": 11},
        )

        with (
            patch("app.agents.dono.build_toolkit") as build_toolkit,
            patch("app.agents.dono.run_agent_with_tools", new=AsyncMock(return_value=fake_response)) as run_agent,
        ):
            build_toolkit.return_value = []
            result = await dono._node_agente_dono_native(_state("DONO"))

        requested_names = build_toolkit.call_args.args[0]
        self.assertEqual(
            set(requested_names),
            {"get_kpis", "get_sales_summary", "get_stock_alerts", "get_kpis_by_store", "retrieve_with_sources"},
        )
        run_agent.assert_awaited_once()
        self.assertEqual(result["agent_response"], "resposta nativa")
        self.assertEqual(result["input_tokens"], 5)


if __name__ == "__main__":
    unittest.main()
