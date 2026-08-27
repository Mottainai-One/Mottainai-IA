"""Access-control tests for sessions, roles, and tenant isolation."""
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from pydantic import ValidationError

from app.agents.supervisor import node_supervisor_route
from app.main import ChatRequest, get_chat_history
from app.memory import short_term
from app.security.auth import AuthContext
from app.memory.short_term import (
    SessionExpiredError,
    SessionOwnershipError,
    get_conversation,
    get_or_create_conversation,
    save_message,
)


class UpdateResult:
    def __init__(self, modified_count: int = 1):
        self.modified_count = modified_count


class FakeConversations:
    def __init__(self, documents: list[dict] | None = None):
        self.documents = documents or []

    async def find_one(self, query: dict):
        for document in self.documents:
            if all(document.get(key) == value for key, value in query.items()):
                return document
        return None

    async def insert_one(self, document: dict):
        document = {**document, "_id": len(self.documents) + 1}
        self.documents.append(document)

    async def update_one(self, query: dict, update: dict):
        document = await self.find_one(query)
        if document:
            document.update(update.get("$set", {}))
        return UpdateResult(1 if document else 0)


class FakeDatabase:
    def __init__(self, documents: list[dict] | None = None):
        self.conversations = FakeConversations(documents)
        self.messages = FakeMessages()


class FakeMessages:
    def __init__(self):
        self.documents: list[dict] = []

    async def insert_one(self, document: dict):
        self.documents.append(document)


class SessionIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_creates_session_for_its_company_and_user(self):
        db = FakeDatabase()
        with patch.object(short_term, "get_mongo_db", return_value=db):
            session = await get_or_create_conversation("session-1", empresa_id=1, usuario_id=10)

        self.assertEqual(session["empresaId"], 1)
        self.assertEqual(session["usuarioId"], 10)
        self.assertEqual(session["status"], "active")

    async def test_rejects_session_of_another_user(self):
        db = FakeDatabase([_active_session(usuario_id=10)])
        with patch.object(short_term, "get_mongo_db", return_value=db):
            with self.assertRaises(SessionOwnershipError):
                await get_or_create_conversation("session-1", empresa_id=1, usuario_id=11)

    async def test_rejects_session_of_another_company(self):
        db = FakeDatabase([_active_session(empresa_id=1)])
        with patch.object(short_term, "get_mongo_db", return_value=db):
            with self.assertRaises(SessionOwnershipError):
                await get_or_create_conversation("session-1", empresa_id=2, usuario_id=10)

    async def test_expires_inactive_session(self):
        db = FakeDatabase([_active_session(last_interaction=datetime.now(timezone.utc) - timedelta(hours=2))])
        with patch.object(short_term, "get_mongo_db", return_value=db):
            with self.assertRaises(SessionExpiredError):
                await get_or_create_conversation("session-1", empresa_id=1, usuario_id=10)

        self.assertEqual(db.conversations.documents[0]["status"], "expired")

    async def test_history_endpoint_denies_another_user(self):
        with patch("interfaces.api.main.get_conversation", new=AsyncMock(side_effect=SessionOwnershipError("forbidden"))):
            with self.assertRaises(HTTPException) as context:
                await get_chat_history("session-1", AuthContext(usuario_id=999, empresa_id=1, role="CLIENTE"))

        self.assertEqual(context.exception.status_code, 403)

    async def test_get_conversation_denies_another_company(self):
        db = FakeDatabase([_active_session(empresa_id=1)])
        with patch.object(short_term, "get_mongo_db", return_value=db):
            with self.assertRaises(SessionOwnershipError):
                await get_conversation("session-1", empresa_id=2, usuario_id=10)

    async def test_message_inherits_tenant_from_its_conversation(self):
        db = FakeDatabase([_active_session(empresa_id=42)])
        with patch.object(short_term, "get_mongo_db", return_value=db):
            await save_message("session-1", role="user", content="teste")

        self.assertEqual(db.messages.documents[0]["empresaId"], 42)


def _active_session(
    empresa_id: int = 1,
    usuario_id: int = 10,
    last_interaction: datetime | None = None,
) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "_id": 1,
        "sessionId": "session-1",
        "empresaId": empresa_id,
        "usuarioId": usuario_id,
        "status": "active",
        "startedAt": now,
        "lastInteraction": last_interaction or now,
    }


class RoleRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_routes_operational_roles_to_employee_agent(self):
        for role in ("ESTOQUISTA", "GERENTE"):
            result = await node_supervisor_route(_state(role, "Quais alertas estão ativos?"))
            self.assertEqual(result["selected_agent"], "funcionario")

    async def test_routes_owner_to_owner_agent(self):
        result = await node_supervisor_route(_state("DONO", "Mostre o faturamento do mês"))
        self.assertEqual(result["selected_agent"], "dono")

    async def test_routes_predictive_owner_question_to_predictive_engine(self):
        result = await node_supervisor_route(_state("DONO", "Qual a previsão de demanda?"))
        self.assertEqual(result["selected_agent"], "motor_preditivo")

    async def test_routes_customer_faq_without_operational_access(self):
        result = await node_supervisor_route(_state("CLIENTE", "Como funciona a fidelidade?"))
        self.assertEqual(result["selected_agent"], "faq")

    async def test_routes_customer_promotion_question_to_customer_agent(self):
        result = await node_supervisor_route(_state("CLIENTE", "Quais promoções estão ativas?"))
        self.assertEqual(result["selected_agent"], "cliente")


def _state(role: str, message: str) -> dict:
    return {"user_role": role, "sanitized_input": message, "error": None}


class RequestValidationTests(unittest.TestCase):
    def test_rejects_identity_claims_in_payload(self):
        with self.assertRaises(ValidationError):
            ChatRequest(
                message="teste",
                session_id="session-1",
                empresa_id=1,
                usuario_id=10,
                user_role="ADMINISTRADOR",
            )

    def test_accepts_only_message_and_session_id(self):
        request = ChatRequest(message="teste", session_id="session-1")
        self.assertEqual(request.message, "teste")


if __name__ == "__main__":
    unittest.main()
