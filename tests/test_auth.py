"""JWT authentication and role-based authorization tests."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import jwt
from fastapi import HTTPException

from app.security.auth import decode_access_token, require_roles

SECRET = "a" * 32
SETTINGS = SimpleNamespace(jwt_secret=SECRET, jwt_algorithm="HS256")


def token(**claims: object) -> str:
    payload = {"sub": "10", "empresa_id": 1, "role": "CLIENTE", "exp": datetime.now(timezone.utc) + timedelta(minutes=5)}
    payload.update(claims)
    return jwt.encode(payload, SECRET, algorithm="HS256")


class JwtAuthenticationTests(unittest.TestCase):
    def test_extracts_verified_identity_from_claims(self):
        with patch("app.security.auth.get_settings", return_value=SETTINGS):
            principal = decode_access_token(token(role="DONO", empresa_id=7))

        self.assertEqual((principal.usuario_id, principal.empresa_id, principal.role), (10, 7, "DONO"))

    def test_rejects_expired_token(self):
        with patch("app.security.auth.get_settings", return_value=SETTINGS):
            with self.assertRaises(HTTPException) as context:
                decode_access_token(token(exp=datetime.now(timezone.utc) - timedelta(seconds=1)))
        self.assertEqual(context.exception.status_code, 401)

    def test_rejects_removed_administrator_role(self):
        with patch("app.security.auth.get_settings", return_value=SETTINGS):
            with self.assertRaises(HTTPException):
                decode_access_token(token(role="ADMINISTRADOR"))

    def test_rejects_missing_or_weak_secret(self):
        with patch("app.security.auth.get_settings", return_value=SimpleNamespace(jwt_secret="short", jwt_algorithm="HS256")):
            with self.assertRaises(HTTPException):
                decode_access_token(token())

    def test_rejects_known_placeholder_even_when_long_enough(self):
        placeholder = "CHANGE_ME_" + "a" * 32
        settings = SimpleNamespace(jwt_secret=placeholder, jwt_algorithm="HS256")
        placeholder_token = jwt.encode(
            {"sub": "10", "empresa_id": 1, "role": "CLIENTE", "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
            placeholder,
            algorithm="HS256",
        )

        with patch("app.security.auth.get_settings", return_value=settings):
            with self.assertRaises(HTTPException):
                decode_access_token(placeholder_token)


class RoleAuthorizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_owner_only_dependency_denies_client(self):
        dependency = require_roles("DONO")
        with self.assertRaises(HTTPException) as context:
            await dependency(type("Principal", (), {"role": "CLIENTE"})())
        self.assertEqual(context.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
