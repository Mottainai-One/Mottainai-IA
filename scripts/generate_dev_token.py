#!/usr/bin/env python3
"""Gera um JWT local para demonstração; não expõe emissão de token pela API."""
import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import jwt

from app.security.auth import is_configured_jwt_secret
from config.settings import get_settings


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--usuario-id", type=int, required=True)
    parser.add_argument("--empresa-id", type=int, required=True)
    parser.add_argument("--role", choices=["CLIENTE", "ESTOQUISTA", "GERENTE", "DONO"], required=True)
    args = parser.parse_args()

    settings = get_settings()
    if settings.env != "development" or not is_configured_jwt_secret(settings.jwt_secret):
        raise SystemExit("Defina JWT_SECRET forte e use ENV=development para gerar token local.")

    expires_at = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expiration_minutes)
    token = jwt.encode(
        {"sub": str(args.usuario_id), "empresa_id": args.empresa_id, "role": args.role, "exp": expires_at},
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    print(token)


if __name__ == "__main__":
    main()
