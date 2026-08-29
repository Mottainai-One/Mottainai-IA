"""HTTP-level tests: exercise the real ASGI app (routing, header/body
parsing, dependency-injected auth) instead of calling endpoint functions
directly in Python. Direct-call tests elsewhere in this suite are correct
and much faster, but they bypass FastAPI's own request handling — a
request with no Authorization header, or a malformed JSON body, never
reaches those tests because the caller always hands in an already-built
AuthContext/Pydantic model. These tests close that specific gap; they
don't duplicate the extensive direct-call business-logic coverage
elsewhere.

Deliberately never used as `with TestClient(app) as client:` — the `with`
form runs the app's lifespan, which loads the real local embedding model
(app.rag.retriever.get_embedding_model, local_files_only=True). None of
these tests need that, and it would make them depend on the model being
pre-cached on whatever machine runs them. Plain instantiation skips
lifespan entirely and still dispatches real HTTP requests through the app.
"""
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import jwt
from fastapi.testclient import TestClient

from interfaces.api.main import app

JWT_SECRET = "a" * 32


def _signed_token(**claims: object) -> str:
    payload = {
        "sub": "10", "empresa_id": 1, "role": "CLIENTE",
        "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
    }
    payload.update(claims)
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


class LivenessOverHttpTests(unittest.TestCase):
    def test_livez_responds_200_over_real_http(self):
        client = TestClient(app)

        response = client.get("/livez")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "alive"})


class AuthenticationOverHttpTests(unittest.TestCase):
    def test_chat_without_authorization_header_is_rejected_by_the_real_dependency(self):
        client = TestClient(app)

        response = client.post("/chat", json={"message": "oi", "session_id": "s1"})

        self.assertEqual(response.status_code, 401)

    def test_chat_with_a_garbage_bearer_token_is_rejected(self):
        client = TestClient(app)

        response = client.post(
            "/chat",
            json={"message": "oi", "session_id": "s1"},
            headers={"Authorization": "Bearer not-a-real-jwt"},
        )

        self.assertEqual(response.status_code, 401)

    def test_chat_with_a_malformed_body_returns_422_not_401(self):
        # A valid signature but a body missing the required session_id —
        # isolates Pydantic's real request-body validation from auth.
        settings = SimpleNamespace(jwt_secret=JWT_SECRET, jwt_algorithm="HS256")
        token = _signed_token()
        client = TestClient(app)

        with patch("app.security.auth.get_settings", return_value=settings):
            response = client.post(
                "/chat",
                json={"message": "oi"},  # session_id missing
                headers={"Authorization": f"Bearer {token}"},
            )

        self.assertEqual(response.status_code, 422)


class McpA2aAuthOverHttpTests(unittest.TestCase):
    def test_mcp_returns_a_json_rpc_error_body_for_a_wrong_token(self):
        settings = SimpleNamespace(mcp_shared_token="the-real-token", mcp_empresa_id=7)
        client = TestClient(app)

        with patch("app.integrations.mcp_a2a.get_settings", return_value=settings):
            response = client.post(
                "/mcp",
                json={"id": 1, "method": "tools/list"},
                headers={"Authorization": "Bearer wrong-token"},
            )

        # JSON-RPC convention: the transport-level HTTP status stays 200,
        # the failure is inside the JSON-RPC error envelope.
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["error"]["code"], -32001)

    def test_mcp_rejects_a_request_with_no_authorization_header_at_all(self):
        settings = SimpleNamespace(mcp_shared_token="the-real-token", mcp_empresa_id=7)
        client = TestClient(app)

        with patch("app.integrations.mcp_a2a.get_settings", return_value=settings):
            response = client.post("/mcp", json={"id": 1, "method": "tools/list"})

        self.assertEqual(response.json()["error"]["code"], -32001)

    def test_a2a_maps_unauthorized_to_a_real_http_401(self):
        settings = SimpleNamespace(a2a_shared_token="the-real-token", a2a_empresa_id=7)
        client = TestClient(app)

        with patch("app.integrations.mcp_a2a.get_settings", return_value=settings):
            response = client.post(
                "/a2a",
                json={"action": "get_company_kpis", "payload": {}},
                headers={"Authorization": "Bearer wrong-token"},
            )

        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
