"""Verify Firebase client ID tokens with Google's rotating public certificates."""
import asyncio
import re
import time
from dataclasses import dataclass

import httpx
import jwt
from cryptography import x509

from config.settings import get_settings

_CERTIFICATES_URL = "https://www.googleapis.com/robot/v1/metadata/x509/securetoken@system.gserviceaccount.com"
_certificates: dict[str, str] = {}
_expires_at = 0.0
_fetched_at = 0.0
_certificate_lock = asyncio.Lock()


class FirebaseVerificationUnavailable(RuntimeError):
    """The trusted certificate service cannot currently verify the token."""


@dataclass(frozen=True)
class FirebaseIdentity:
    uid: str
    email: str
    expires_at: int


async def _signing_key(kid: str):
    global _certificates, _expires_at, _fetched_at
    now = time.monotonic()
    if now >= _expires_at or (kid not in _certificates and now - _fetched_at > 60):
        async with _certificate_lock:
            now = time.monotonic()
            if now >= _expires_at or (kid not in _certificates and now - _fetched_at > 60):
                try:
                    async with httpx.AsyncClient(timeout=10) as client:
                        response = await client.get(_CERTIFICATES_URL)
                    response.raise_for_status()
                    certificates = response.json()
                    if not isinstance(certificates, dict) or not certificates:
                        raise ValueError("Invalid certificate response")
                    if not all(isinstance(k, str) and isinstance(v, str) for k, v in certificates.items()):
                        raise ValueError("Invalid certificate response")
                    match = re.search(r"max-age=(\d+)", response.headers.get("cache-control", ""))
                    ttl = min(int(match[1]), 3600) if match else 300
                    _certificates = certificates
                    _fetched_at = now
                    _expires_at = now + ttl
                except (httpx.HTTPError, ValueError) as exc:
                    raise FirebaseVerificationUnavailable("Firebase certificate verification unavailable") from exc
    certificate = _certificates.get(kid)
    if not certificate:
        raise jwt.InvalidTokenError("Unknown Firebase signing key")
    try:
        return x509.load_pem_x509_certificate(certificate.encode()).public_key()
    except ValueError as exc:
        raise FirebaseVerificationUnavailable("Invalid Firebase signing certificate") from exc


async def verify_firebase_id_token(token: str) -> FirebaseIdentity:
    """Do not trust role/company claims; SQL resolves those after verification."""
    settings = get_settings()
    if not settings.firebase_auth_enabled or not settings.firebase_project_id:
        raise jwt.InvalidTokenError("Firebase authentication is disabled")
    header = jwt.get_unverified_header(token)
    kid = header.get("kid")
    if header.get("alg") != "RS256" or not isinstance(kid, str) or not 0 < len(kid) <= 128:
        raise jwt.InvalidTokenError("Invalid Firebase signing header")
    claims = jwt.decode(
        token, await _signing_key(kid), algorithms=["RS256"],
        audience=settings.firebase_project_id,
        issuer=f"https://securetoken.google.com/{settings.firebase_project_id}",
        options={"require": ["exp", "iat", "auth_time", "sub", "aud", "iss", "email"]},
    )
    uid, email = claims["sub"], claims["email"]
    if not isinstance(uid, str) or not 0 < len(uid) <= 128:
        raise jwt.InvalidTokenError("Invalid Firebase UID")
    if not isinstance(email, str) or not email.strip() or len(email) > 320:
        raise jwt.InvalidTokenError("Firebase email is required")
    for field in ("iat", "auth_time", "exp"):
        if type(claims[field]) is not int or claims[field] < 0:
            raise jwt.InvalidTokenError("Invalid Firebase timestamp")
    if claims["auth_time"] > time.time() or claims["auth_time"] > claims["iat"]:
        raise jwt.InvalidTokenError("Invalid Firebase authentication time")
    return FirebaseIdentity(uid, email.strip().lower(), claims["exp"])
