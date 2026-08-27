#!/usr/bin/env python3
"""Valida, em modo somente leitura, a disponibilidade do schema Mottainai v6."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.database.operational_schema import OPERATIONAL_SCHEMA_READY_QUERY
from config.settings import get_settings


async def main() -> None:
    engine = create_async_engine(get_settings().postgres_dsn, pool_pre_ping=True)
    try:
        async with engine.connect() as connection:
            result = await connection.execute(
                text(OPERATIONAL_SCHEMA_READY_QUERY)
            )
            if not result.scalar():
                raise RuntimeError("schema operacional Mottainai v6 não está disponível")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        print(
            "ERRO: não foi possível conectar ao PostgreSQL configurado ou validar o schema Mottainai.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    print("[postgres-preflight] PostgreSQL e schema operacional disponíveis.")
