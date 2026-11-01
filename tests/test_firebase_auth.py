"""Cryptographic Firebase verification and authoritative SQL role binding."""
import time
import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

from app.database.identity import CustomerIdentity, OperationalIdentity, resolve_firebase_identity
from app.security.auth import decode_access_token
from app.security.firebase_auth import FirebaseVerificationUnavailable, verify_firebase_id_token

PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
SETTINGS = SimpleNamespace(firebase_auth_enabled=True, firebase_project_id="test-project")


def token(**overrides):
    now = int(time.time())
    claims = {"sub":"firebase-uid", "email":"Staff@Example.com", "iat":now,
              "exp":now+3600, "auth_time":now-10, "aud":"test-project",
              "iss":"https://securetoken.google.com/test-project"}
    claims.update(overrides)
    return jwt.encode(claims, PRIVATE_KEY, algorithm="RS256", headers={"kid":"test-key"})


class FirebaseSignatureTests(unittest.IsolatedAsyncioTestCase):
    async def verify(self, encoded):
        with (patch("app.security.firebase_auth.get_settings", return_value=SETTINGS),
              patch("app.security.firebase_auth._signing_key", AsyncMock(return_value=PRIVATE_KEY.public_key()))):
            return await verify_firebase_id_token(encoded)

    async def test_valid_signature_extracts_uid_and_normalized_email(self):
        identity = await self.verify(token())
        self.assertEqual(identity.uid, "firebase-uid")
        self.assertEqual(identity.email, "staff@example.com")

    async def test_wrong_project_issuer_expiry_and_future_auth_time_are_rejected(self):
        for changes in ({"aud":"other-project"}, {"iss":"https://evil.example"},
                        {"exp":int(time.time())-1}, {"auth_time":int(time.time())+60},
                        {"auth_time":True}, {"sub":""}, {"email":""}):
            with self.subTest(changes=changes), self.assertRaises(jwt.PyJWTError):
                await self.verify(token(**changes))

    async def test_forged_signature_cannot_use_a_valid_kid(self):
        other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        encoded = token()
        claims = jwt.decode(encoded, options={"verify_signature":False})
        forged = jwt.encode(claims, other, algorithm="RS256", headers={"kid":"test-key"})
        with self.assertRaises(jwt.InvalidSignatureError):
            await self.verify(forged)

    async def test_disabled_firebase_does_not_fetch_certificates(self):
        with (patch("app.security.firebase_auth.get_settings", return_value=SimpleNamespace(firebase_auth_enabled=False)),
              patch("app.security.firebase_auth._signing_key", AsyncMock()) as fetch):
            with self.assertRaises(jwt.PyJWTError):
                await verify_firebase_id_token(token())
        fetch.assert_not_awaited()

    async def test_roles_and_company_come_from_sql_not_firebase_claims(self):
        actor = OperationalIdentity(1003, 7, "ESTOQUISTA", "EMAIL_SHA256_"+"A"*64)
        with (patch("app.security.auth.get_settings", return_value=SETTINGS),
              patch("app.security.firebase_auth.get_settings", return_value=SETTINGS),
              patch("app.security.firebase_auth._signing_key", AsyncMock(return_value=PRIVATE_KEY.public_key())),
              patch("app.security.auth._is_revoked", AsyncMock(return_value=False)),
              patch("app.database.identity.resolve_firebase_customer_identity", AsyncMock(return_value=None)),
              patch("app.database.identity.resolve_firebase_identity", AsyncMock(return_value=actor)) as resolve):
            principal = await decode_access_token(token(role="DONO", empresa_id=999))
        self.assertEqual((principal.usuario_id,principal.empresa_id,principal.role),(1003,7,"ESTOQUISTA"))
        self.assertTrue(principal.jti.startswith("firebase-"))
        resolve.assert_awaited_once_with("firebase-uid")

    async def test_customer_identity_sets_cliente_without_staff_resolution(self):
        with (patch("app.security.auth.get_settings", return_value=SETTINGS),
              patch("app.security.firebase_auth.get_settings", return_value=SETTINGS),
              patch("app.security.firebase_auth._signing_key", AsyncMock(return_value=PRIVATE_KEY.public_key())),
              patch("app.security.auth._is_revoked", AsyncMock(return_value=False)),
              patch("app.database.identity.resolve_firebase_customer_identity",
                    AsyncMock(return_value=CustomerIdentity(51, 1))),
              patch("app.database.identity.resolve_firebase_identity", AsyncMock()) as resolve_staff):
            principal = await decode_access_token(token())
        self.assertEqual((principal.usuario_id, principal.empresa_id, principal.role), (-51, 1, "CLIENTE"))
        self.assertIsNone(principal.operational_identity)
        resolve_staff.assert_not_awaited()

    async def test_certificate_outage_is_unavailable_not_authenticated(self):
        with (patch("app.security.auth.get_settings", return_value=SETTINGS),
              patch("app.security.firebase_auth.verify_firebase_id_token", AsyncMock(side_effect=FirebaseVerificationUnavailable()))):
            with self.assertRaises(HTTPException) as raised:
                await decode_access_token(token())
        self.assertEqual(raised.exception.status_code,503)

    async def test_locally_revoked_firebase_token_is_rejected_before_sql(self):
        with (patch("app.security.auth.get_settings", return_value=SETTINGS),
              patch("app.security.firebase_auth.get_settings", return_value=SETTINGS),
              patch("app.security.firebase_auth._signing_key", AsyncMock(return_value=PRIVATE_KEY.public_key())),
              patch("app.security.auth._is_revoked", AsyncMock(return_value=True)),
              patch("app.database.identity.resolve_firebase_identity", AsyncMock()) as resolve):
            with self.assertRaises(HTTPException) as raised:
                await decode_access_token(token())
        self.assertEqual(raised.exception.status_code,401)
        resolve.assert_not_awaited()


class FirebaseSqlBindingTests(unittest.IsolatedAsyncioTestCase):
    async def test_customer_resolver_uses_hashed_uid_and_customer_rls_role(self):
        import hashlib

        from app.database.identity import resolve_firebase_customer_identity

        session = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: 51)))
        @asynccontextmanager
        async def get_session():
            yield session
        with (patch("app.database.postgres.get_pg_session", get_session),
              patch("app.config.get_settings", return_value=SimpleNamespace(customer_chat_empresa_id=1))):
            identity = await resolve_firebase_customer_identity(" bound-uid ")
        self.assertEqual(identity, CustomerIdentity(51, 1))
        self.assertIn("SET LOCAL ROLE mottainai_customer_api", str(session.execute.await_args_list[0].args[0]))
        self.assertIn("fn_bootstrap_customer_context", str(session.execute.await_args_list[1].args[0]))
        self.assertEqual(
            session.execute.await_args_list[1].args[1]["external_auth_token"],
            "AUTH_SHA256_" + hashlib.sha256(b"bound-uid").hexdigest().upper(),
        )
    async def test_maps_all_staff_roles_using_existing_uid_column(self):
        for db_role, role in (("ADMINISTRATOR","DONO"),("MANAGER","GERENTE"),("OPERATOR","ESTOQUISTA")):
            row={"user_id":1003,"company_id":7,"store_id":11,"database_role":db_role}
            session=SimpleNamespace(execute=AsyncMock(side_effect=[
                SimpleNamespace(scalar_one=lambda:True),
                SimpleNamespace(mappings=lambda:SimpleNamespace(first=lambda:row))]))
            @asynccontextmanager
            async def get_session():
                yield session
            with patch("app.database.postgres.get_pg_session", get_session):
                identity=await resolve_firebase_identity("bound-uid")
            self.assertEqual(identity.role,role)
            self.assertEqual(identity.login_identity, "bound-uid")
            self.assertEqual(identity.store_id, 11)
            query, params=session.execute.await_args_list[1].args
            self.assertIn("u.firebase_uid=:uid",str(query))
            self.assertIn("'FIREBASE_UID'", str(session.execute.await_args_list[0].args[0]))
            self.assertEqual(params["uid"],"bound-uid")
            self.assertNotIn("u.email", str(query))
            self.assertNotIn("identity", params)
            self.assertEqual(session.execute.await_args_list[0].args[1]["identity"], "bound-uid")

    async def test_missing_uid_binding_cannot_login_by_email_alone(self):
        session=SimpleNamespace(execute=AsyncMock(side_effect=[
            SimpleNamespace(scalar_one=lambda:True),
            SimpleNamespace(mappings=lambda:SimpleNamespace(first=lambda:None))]))
        @asynccontextmanager
        async def get_session():
            yield session
        with patch("app.database.postgres.get_pg_session",get_session):
            with self.assertRaises(PermissionError):
                await resolve_firebase_identity("unbound-uid")

    async def test_inactive_operational_account_is_denied(self):
        session=SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar_one=lambda:False)))
        @asynccontextmanager
        async def get_session():
            yield session
        with patch("app.database.postgres.get_pg_session",get_session):
            with self.assertRaises(PermissionError):
                await resolve_firebase_identity("bound-uid")
        session.execute.assert_awaited_once()
