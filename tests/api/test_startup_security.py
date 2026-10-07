"""Startup must reject documented JWT placeholders before loading dependencies."""

import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from config.settings import Settings
from interfaces.api.main import app, lifespan


def _example_jwt_secret() -> str:
    example = Path(__file__).resolve().parents[2] / ".env.example"
    line = next(line for line in example.read_text(encoding="utf-8").splitlines()
                if line.startswith("JWT_SECRET="))
    return line.partition("=")[2]


class StartupJwtSecretTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_missing_weak_and_documented_placeholder_secrets(self):
        for secret in ("", "short", _example_jwt_secret(),
                       "gere-um-segredo-forte-de-32-caracteres-por-maquina"):
            with (
                self.subTest(secret=secret),
                patch(
                    "interfaces.api.main.settings",
                    Settings(_env_file=None, env="development", jwt_secret=secret),
                ),
                patch("app.database.postgres.validate_pg_security", new_callable=AsyncMock),
            patch("app.rag.retriever.get_embedding_model") as load_model,
            ):
                with self.assertRaisesRegex(RuntimeError, "JWT_SECRET"):
                    async with lifespan(app):
                        pass
                load_model.assert_not_called()

    async def test_accepts_a_configured_secret_and_starts_normally(self):
        settings = Settings(
            _env_file=None, env="development", jwt_secret="a" * 32,
            transformers_offline=False,
        )
        with (
            patch("interfaces.api.main.settings", settings),
            patch("app.database.postgres.validate_pg_security", new_callable=AsyncMock),
            patch("app.rag.retriever.get_embedding_model") as load_model,
            patch("app.database.redis_client.close_redis_pool", new_callable=AsyncMock) as close_pool,
            patch("app.database.mongo.close_mongo_client", new_callable=AsyncMock),
            patch("app.database.postgres.close_pg_engine", new_callable=AsyncMock),
            patch("app.agents.runtime.close_llm_clients", new_callable=AsyncMock),
        ):
            async with lifespan(app):
                load_model.assert_called_once_with()
            close_pool.assert_awaited_once_with()
