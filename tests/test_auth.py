"""JWT authentication, revocation and role-based authorization tests."""
import unittest
import base64
from unittest.mock import Mock
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import jwt
from fastapi import HTTPException

from app.security.auth import decode_access_token, require_roles, revoke_token

SECRET = "a" * 32
SETTINGS = SimpleNamespace(jwt_secret=SECRET, jwt_algorithm="HS256")


def token(**claims: object) -> str:
    payload = {"sub": "10", "empresa_id": 1, "role": "CLIENTE", "use": "access", "exp": datetime.now(timezone.utc) + timedelta(minutes=5)}
    payload.update(claims)
    return jwt.encode(payload, SECRET, algorithm="HS256")


class JwtAuthenticationTests(unittest.IsolatedAsyncioTestCase):
    async def test_extracts_verified_identity_from_claims(self):
        with patch("app.security.auth.get_settings", return_value=SETTINGS):
            principal = await decode_access_token(token(role="DONO", empresa_id=7))

        self.assertEqual((principal.usuario_id, principal.empresa_id, principal.role), (10, 7, "DONO"))

    async def test_accepts_sql_api_staff_token_with_base64_key_and_numeric_identity_claims(self):
        raw_key = b"k" * 32
        encoded_key = base64.b64encode(raw_key).decode("ascii")
        settings = SimpleNamespace(
            jwt_secret=encoded_key,
            jwt_secret_base64=True,
            jwt_algorithm="HS256",
            jwt_issuer="https://mottainai.local",
            jwt_audience="",
        )
        sql_api_claims = {
            "sub": "firebase-staff-uid",
            "usuario_id": 41,
            "empresa_id": 7,
            "store_id": 3,
            "role": "GERENTE",
            "roles": ["MANAGER"],
            "use": "access",
            "iss": "https://mottainai.local",
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        }
        signed = jwt.encode(sql_api_claims, raw_key, algorithm="HS256")

        with patch("app.security.auth.get_settings", return_value=settings):
            principal = await decode_access_token(signed)

        self.assertEqual((principal.usuario_id, principal.empresa_id, principal.store_id, principal.role),
                         (41, 7, 3, "GERENTE"))
        self.assertEqual(principal.firebase_uid, "firebase-staff-uid")

    async def test_accepts_firebase_id_token_and_resolves_staff_scope_from_database(self):
        project_id = "mottainai-operacional"
        claims = {
            "iss": f"https://securetoken.google.com/{project_id}",
            "aud": project_id,
            "sub": "firebase-staff-uid",
            "user_id": 41,
            "role": "ESTOQUISTA",
            "storeId": 3,
            "iat": int(datetime.now(timezone.utc).timestamp()),
            "auth_time": int(datetime.now(timezone.utc).timestamp()),
            "exp": int((datetime.now(timezone.utc) + timedelta(minutes=5)).timestamp()),
        }
        bootstrap_result = Mock()
        bootstrap_result.scalar.return_value = True
        identity_result = Mock()
        identity_result.mappings.return_value.one.return_value = {
            "usuario_id": 41, "empresa_id": 7, "store_id": 3,
        }
        session = AsyncMock()
        session.execute.side_effect = [bootstrap_result, identity_result]

        class SessionContext:
            async def __aenter__(self):
                return session

            async def __aexit__(self, exc_type, exc, traceback):
                return False

        settings = SimpleNamespace(
            firebase_project_id=project_id,
            jwt_secret="",
            jwt_algorithm="HS256",
        )
        firebase_token = jwt.encode({"iss": claims["iss"], "sub": claims["sub"]}, SECRET, algorithm="HS256")
        with (
            patch("app.security.auth.get_settings", return_value=settings),
            patch("app.security.auth.verify_firebase_id_token", return_value=claims),
            patch("app.database.postgres.get_pg_session", return_value=SessionContext()),
        ):
            principal = await decode_access_token(firebase_token)

        self.assertEqual(
            (principal.usuario_id, principal.empresa_id, principal.store_id, principal.role),
            (41, 7, 3, "ESTOQUISTA"),
        )
        self.assertEqual(principal.firebase_uid, "firebase-staff-uid")

    async def test_rejects_refresh_token_for_business_routes(self):
        with patch("app.security.auth.get_settings", return_value=SETTINGS):
            with self.assertRaises(HTTPException):
                await decode_access_token(token(use="refresh"))

    async def test_rejects_expired_token(self):
        with patch("app.security.auth.get_settings", return_value=SETTINGS):
            with self.assertRaises(HTTPException) as context:
                await decode_access_token(token(exp=datetime.now(timezone.utc) - timedelta(seconds=1)))
        self.assertEqual(context.exception.status_code, 401)

    async def test_rejects_removed_administrator_role(self):
        with patch("app.security.auth.get_settings", return_value=SETTINGS):
            with self.assertRaises(HTTPException):
                await decode_access_token(token(role="ADMINISTRADOR"))

    async def test_enforces_configured_issuer_and_audience(self):
        settings = SimpleNamespace(
            jwt_secret=SECRET,
            jwt_algorithm="HS256",
            jwt_issuer="mottainai-auth",
            jwt_audience="mottainai-api",
        )
        with patch("app.security.auth.get_settings", return_value=settings):
            principal = await decode_access_token(
                token(iss="mottainai-auth", aud="mottainai-api")
            )
            with self.assertRaises(HTTPException):
                await decode_access_token(token(iss="other-issuer", aud="mottainai-api"))
        self.assertEqual(principal.usuario_id, 10)

    async def test_rejects_missing_or_weak_secret(self):
        with patch("app.security.auth.get_settings", return_value=SimpleNamespace(jwt_secret="short", jwt_algorithm="HS256")):
            with self.assertRaises(HTTPException):
                await decode_access_token(token())

    async def test_rejects_known_placeholder_even_when_long_enough(self):
        placeholder = "CHANGE_ME_" + "a" * 32
        settings = SimpleNamespace(jwt_secret=placeholder, jwt_algorithm="HS256")
        placeholder_token = jwt.encode(
            {"sub": "10", "empresa_id": 1, "role": "CLIENTE", "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
            placeholder,
            algorithm="HS256",
        )

        with patch("app.security.auth.get_settings", return_value=settings):
            with self.assertRaises(HTTPException):
                await decode_access_token(placeholder_token)

    async def test_token_without_jti_decodes_with_no_revocation_lookup(self):
        with patch("app.security.auth.get_settings", return_value=SETTINGS):
            principal = await decode_access_token(token())

        self.assertIsNone(principal.jti)

    async def test_token_with_jti_is_exposed_on_the_principal(self):
        with patch("app.security.auth.get_settings", return_value=SETTINGS):
            principal = await decode_access_token(token(jti="abc-123"))

        self.assertEqual(principal.jti, "abc-123")


class TokenRevocationTests(unittest.IsolatedAsyncioTestCase):
    async def test_revoked_jti_is_rejected_even_with_a_valid_signature(self):
        redis = AsyncMock()
        redis.exists = AsyncMock(return_value=1)
        with (
            patch("app.security.auth.get_settings", return_value=SETTINGS),
            patch("app.database.redis_client.get_redis", return_value=redis),
        ):
            with self.assertRaises(HTTPException) as context:
                await decode_access_token(token(jti="revoked-1"))

        self.assertEqual(context.exception.status_code, 401)

    async def test_non_revoked_jti_is_accepted(self):
        redis = AsyncMock()
        redis.exists = AsyncMock(return_value=0)
        with (
            patch("app.security.auth.get_settings", return_value=SETTINGS),
            patch("app.database.redis_client.get_redis", return_value=redis),
        ):
            principal = await decode_access_token(token(jti="fine-1"))

        self.assertEqual(principal.jti, "fine-1")

    async def test_revocation_check_fails_open_when_redis_is_down(self):
        with (
            patch("app.security.auth.get_settings", return_value=SETTINGS),
            patch("app.database.redis_client.get_redis", side_effect=ConnectionError("down")),
        ):
            principal = await decode_access_token(token(jti="whatever"))

        self.assertEqual(principal.jti, "whatever")

    async def test_revoke_token_writes_a_deny_list_entry_with_remaining_ttl(self):
        redis = AsyncMock()
        exp = int(datetime.now(timezone.utc).timestamp()) + 120
        with patch("app.database.redis_client.get_redis", return_value=redis):
            await revoke_token("some-jti", exp)

        redis.set.assert_awaited_once()
        args, kwargs = redis.set.await_args
        self.assertIn("some-jti", args[0])
        self.assertLessEqual(kwargs["ex"], 120)
        self.assertGreater(kwargs["ex"], 0)

    async def test_revoke_token_is_a_noop_for_an_already_expired_token(self):
        redis = AsyncMock()
        expired = int(datetime.now(timezone.utc).timestamp()) - 10
        with patch("app.database.redis_client.get_redis", return_value=redis):
            await revoke_token("some-jti", expired)

        redis.set.assert_not_awaited()

    async def test_revoke_token_propagates_redis_failures_instead_of_swallowing_them(self):
        redis = AsyncMock()
        redis.set = AsyncMock(side_effect=ConnectionError("down"))
        exp = int(datetime.now(timezone.utc).timestamp()) + 120
        with patch("app.database.redis_client.get_redis", return_value=redis):
            with self.assertRaises(ConnectionError):
                await revoke_token("some-jti", exp)


class RoleAuthorizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_owner_only_dependency_denies_client(self):
        dependency = require_roles("DONO")
        with self.assertRaises(HTTPException) as context:
            await dependency(type("Principal", (), {"role": "CLIENTE"})())
        self.assertEqual(context.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
