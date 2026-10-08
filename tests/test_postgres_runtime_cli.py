"""Credential-safe CLI failures without contacting a database."""
import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from scripts import configure_postgres_runtime as setup


class RuntimeSetupOutputTests(unittest.TestCase):
    def test_generated_password_in_failed_create_role_is_never_printed(self):
        password = "synthetic-password-for-output-regression"
        admin = SimpleNamespace(scalar_one=lambda: True)
        absent = SimpleNamespace(scalar_one=lambda: False)

        async def execute(statement, *args):
            sql = str(statement)
            if sql.startswith("CREATE ROLE"):
                self.assertIn(password, sql)
                raise RuntimeError(f"Database error executing SQL: {sql}")
            return admin if "SELECT rolsuper" in sql else absent

        connection = SimpleNamespace(execute=AsyncMock(side_effect=execute))
        transaction = AsyncMock()
        transaction.__aenter__.return_value = connection
        engine = SimpleNamespace(begin=lambda: transaction, dispose=AsyncMock())
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.dict(setup.os.environ, {
            "POSTGRES_ADMIN_DSN": "postgresql+asyncpg://admin:synthetic-admin-password@localhost/test",
            "POSTGRES_APP_PASSWORD": "",
        }), patch.object(setup.secrets, "token_urlsafe", return_value=password), patch.object(setup, "create_async_engine", return_value=engine), redirect_stdout(stdout), redirect_stderr(stderr):
            status = setup.run_cli()
        self.assertEqual(status, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("RuntimeError", stderr.getvalue())
        for sensitive in (password, "synthetic-admin-password", "CREATE ROLE", "Traceback"):
            self.assertNotIn(sensitive, stdout.getvalue() + stderr.getvalue())
        engine.dispose.assert_awaited_once()

    def test_connection_error_details_and_password_are_never_printed(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        failure = RuntimeError("connection DSN postgresql://admin:synthetic-secret@localhost/test")
        with patch.object(setup, "main", new=AsyncMock(side_effect=failure)), redirect_stdout(stdout), redirect_stderr(stderr):
            status = setup.run_cli()
        self.assertEqual(status, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertNotIn("synthetic-secret", stderr.getvalue())
        self.assertNotIn("postgresql://", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())
