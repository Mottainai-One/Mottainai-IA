"""Firebase ID-token verification for the existing Android client contract."""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from typing import Any

from google.auth import exceptions as google_auth_exceptions
from google.auth.transport.requests import Request
from google.oauth2 import id_token

_CERTS_URL = "https://www.googleapis.com/robot/v1/metadata/x509/securetoken@system.gserviceaccount.com"
_cache_lock = threading.Lock()
_cached_certificates: dict[str, Any] | None = None
_cache_expires_at = 0.0


@dataclass
class _CachedHttpResponse:
    status: int
    headers: dict[str, str]
    data: bytes


class _CachedGoogleRequest:
    """Cache Google's rotating Firebase signing certificates per Cache-Control."""

    def __init__(self) -> None:
        self._request = Request()

    def __call__(self, url: str, method: str = "GET", **kwargs: Any) -> Any:
        global _cached_certificates, _cache_expires_at
        if url != _CERTS_URL:
            return self._request(url, method=method, timeout=6, **kwargs)

        now = time.monotonic()
        with _cache_lock:
            if _cached_certificates is not None and now < _cache_expires_at:
                return _CachedHttpResponse(200, {}, _cached_certificates)

            response = self._request(url, method=method, timeout=6, **kwargs)
            if response.status != 200:
                return response

            max_age = 300
            cache_control = response.headers.get("cache-control", "")
            match = re.search(r"(?:^|,)\s*max-age=(\d+)", cache_control, re.IGNORECASE)
            if match:
                max_age = max(60, int(match.group(1)))
            _cached_certificates = response.data
            _cache_expires_at = time.monotonic() + max_age
            return response


_cached_request = _CachedGoogleRequest()


def verify_firebase_id_token(token: str, project_id: str) -> dict[str, Any]:
    """Verify a client Firebase ID token and return only signed claims."""
    if not project_id:
        raise ValueError("FIREBASE_PROJECT_ID is not configured")
    claims = id_token.verify_firebase_token(token, _cached_request, audience=project_id)
    if claims.get("iss") != f"https://securetoken.google.com/{project_id}":
        raise ValueError("Firebase token issuer does not match the configured project")
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject.strip():
        raise ValueError("Firebase token subject is missing")
    now = time.time()
    for time_claim in ("iat", "auth_time"):
        value = claims.get(time_claim)
        if not isinstance(value, (int, float)) or value > now + 60:
            raise ValueError(f"Firebase token has an invalid {time_claim} claim")
    return claims


def firebase_provider_unavailable(exc: Exception) -> bool:
    return isinstance(exc, google_auth_exceptions.TransportError)
