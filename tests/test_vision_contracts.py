import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.agents.visao import analyze_shelf


class VisionMimeContracts(unittest.IsolatedAsyncioTestCase):
    async def test_preserves_validated_upload_mime_type_for_model(self):
        class Model:
            def __init__(self):
                self.message = None

            async def ainvoke(self, messages):
                self.message = messages[0]
                return SimpleNamespace(
                    content='{"produtos_detectados":[],"slots_vazios":{"total_estimado":0,"descricao":""},"ocupacao_pct":0,"estado_geral":"inválido","acoes_sugeridas":[],"confianca_analise":1.0}'
                )

        model = Model()
        with patch("app.agents.visao.get_vision_llm", return_value=model):
            await analyze_shelf(
                image_bytes=b"webp-bytes",
                image_mime_type="image/webp",
                empresa_id=0,
            )

        image = model.message.content[1]["image_url"]["url"]
        self.assertTrue(image.startswith("data:image/webp;base64,"))

    async def test_empty_shelf_keeps_inventory_crosscheck(self):
        model = SimpleNamespace(
            ainvoke=AsyncMock(
                return_value=SimpleNamespace(
                    content='{"produtos_detectados":[],"slots_vazios":{"total_estimado":4,"descricao":""},"ocupacao_pct":0,"estado_geral":"crítico","acoes_sugeridas":[],"confianca_analise":0.9}'
                )
            )
        )
        crosscheck = AsyncMock(return_value={"alertas_ativos": [{"id": 1}]})
        with (
            patch("app.agents.visao.get_vision_llm", return_value=model),
            patch("app.agents.visao.crosscheck_with_inventory", new=crosscheck),
        ):
            result = await analyze_shelf(image_bytes=b"png-bytes", empresa_id=42, store_id=7)

        self.assertEqual(result["cruzamento_inventario"]["alertas_ativos"], [{"id": 1}])
        crosscheck.assert_awaited_once_with(empresa_id=42, store_id=7, detected_products=[])

    async def test_persists_scoped_session_context(self):
        class Results:
            def __init__(self):
                self.documents = []

            async def insert_one(self, document):
                self.documents.append(document)

        results = Results()
        database = SimpleNamespace(ai_results=results)
        model = SimpleNamespace(
            ainvoke=AsyncMock(
                return_value=SimpleNamespace(
                    content='{"produtos_detectados":[],"slots_vazios":{"total_estimado":0,"descricao":""},"ocupacao_pct":100,"estado_geral":"adequado","acoes_sugeridas":[],"confianca_analise":1.0}'
                )
            )
        )
        with (
            patch("app.agents.visao.get_vision_llm", return_value=model),
            patch("app.agents.visao.crosscheck_with_inventory", new=AsyncMock(return_value={})),
            patch("app.database.mongo.get_mongo_db", return_value=database),
        ):
            await analyze_shelf(
                image_bytes=b"png-bytes",
                empresa_id=42,
                usuario_id=7,
                session_id="session-1",
                conversation_id="conversation-1",
            )

        document = results.documents[0]
        self.assertEqual(document["empresaId"], 42)
        self.assertEqual(document["usuarioId"], 7)
        self.assertEqual(document["sessionId"], "session-1")
        self.assertEqual(document["conversationId"], "conversation-1")


if __name__ == "__main__":
    unittest.main()
