"""Configure a dedicated local runtime role without changing existing passwords.

Run with the current administrative POSTGRES_DSN or POSTGRES_ADMIN_DSN.
Only database permissions and local .env connection settings are changed.
"""
import asyncio
import os
import secrets
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from config.settings import get_settings


async def main() -> None:
    admin_url = make_url(os.getenv("POSTGRES_ADMIN_DSN") or get_settings().postgres_dsn)
    role_name = "mottainai_ia"
    password = os.getenv("POSTGRES_APP_PASSWORD") or secrets.token_urlsafe(36)
    engine = create_async_engine(admin_url)
    try:
        async with engine.begin() as connection:
            admin = (await connection.execute(text(
                "SELECT rolsuper FROM pg_roles WHERE rolname = current_user"
            ))).scalar_one()
            if not admin:
                raise RuntimeError("An administrative DSN is required")
            exists = (await connection.execute(text(
                "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :role)"
            ), {"role": role_name})).scalar_one()
            if exists and not os.getenv("POSTGRES_APP_PASSWORD"):
                raise RuntimeError("Existing role: provide its POSTGRES_APP_PASSWORD; no password is reset")
            if not exists:
                # Quotes are escaped even though generated secrets are URL-safe.
                quoted_password = password.replace("'", "''")
                await connection.execute(text(
                    f"CREATE ROLE {role_name} LOGIN NOSUPERUSER NOBYPASSRLS "
                    f"NOCREATEDB NOCREATEROLE PASSWORD '{quoted_password}'"
                ))
            await connection.execute(text(f"ALTER ROLE {role_name} SET search_path = mottainai, public"))
            await connection.execute(text(f"GRANT mottainai_app TO {role_name}"))
            for table in ("company", "retail_store", "employee", "inventory", "sales_transaction", "purchase_order"):
                await connection.execute(text(f"ALTER TABLE mottainai.{table} FORCE ROW LEVEL SECURITY"))
    finally:
        await engine.dispose()

    runtime_url = admin_url.set(username=role_name, password=password)
    check_engine = create_async_engine(runtime_url)
    try:
        async with check_engine.connect() as connection:
            for table in ("company", "retail_store", "inventory", "sales_transaction"):
                count = (await connection.execute(text(f"SELECT count(*) FROM mottainai.{table}"))).scalar_one()
                if count != 0:
                    raise RuntimeError(f"RLS validation failed for {table}")
    finally:
        await check_engine.dispose()
    env_file = ROOT / ".env"
    lines = env_file.read_text(encoding="utf-8").splitlines()
    updates = {"POSTGRES_DSN": runtime_url.render_as_string(hide_password=False)}
    for key, value in updates.items():
        lines = [line for line in lines if not line.startswith(key + "=")]
        lines.append(f"{key}={value}")
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Runtime role configured: {role_name}; unscoped tenant queries returned zero rows.")


def run_cli() -> int:
    """Report failures without exposing SQL, credentials, or a traceback."""
    try:
        asyncio.run(main())
    except Exception as exc:
        # SQL errors may contain the CREATE ROLE statement and its password.
        print(f"Runtime role setup failed ({type(exc).__name__}); verify administrative access, grants and the existing role password.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(run_cli())
