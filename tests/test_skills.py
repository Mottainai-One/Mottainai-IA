"""Skill platform foundation (PR: feat/skill-platform-foundation):
app/skills/base.py's contract, app/skills/registry.py's TTL-cached read
layer, and app/skills/executor.py's authorize -> validate -> run ->
validate -> audit pipeline.
"""
import asyncio
import sys
import time
import types
import unittest
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError

from app.skills import registry as registry_module
from app.skills.base import SkillManifest, SkillPermissionError, SkillScope, SkillValidationError
from app.skills.executor import execute_skill

FAKE_MODULE_NAME = "app.skills._fixtures_for_tests"


def _register_fake_module(**functions) -> None:
    module = types.ModuleType(FAKE_MODULE_NAME)
    for name, fn in functions.items():
        setattr(module, name, fn)
    sys.modules[FAKE_MODULE_NAME] = module


def _unregister_fake_module() -> None:
    sys.modules.pop(FAKE_MODULE_NAME, None)


def _manifest(**overrides) -> SkillManifest:
    fields = dict(
        empresa_id=1, name="test_skill", version="1.0.0", description="d", owner="o",
        entrypoint=f"{FAKE_MODULE_NAME}:run", timeout_seconds=1.0, max_retries=0,
        scope=SkillScope.READ_ONLY, required_roles=["DONO"],
        input_schema={"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]},
        output_schema={"type": "object", "properties": {"y": {"type": "integer"}}, "required": ["y"]},
        active=True,
    )
    fields.update(overrides)
    return SkillManifest(**fields)


class SkillManifestContractTests(unittest.TestCase):
    def test_rejects_a_non_semver_version(self):
        with self.assertRaises(ValidationError):
            _manifest(version="1.0")

    def test_rejects_an_entrypoint_with_no_colon(self):
        with self.assertRaises(ValidationError):
            _manifest(entrypoint="app.skills.foo")

    def test_rejects_an_entrypoint_outside_the_allowed_prefix(self):
        with self.assertRaises(ValidationError):
            _manifest(entrypoint="os.system:call")

    def test_write_autonomous_is_not_a_valid_scope(self):
        with self.assertRaises(ValueError):
            SkillScope("write_autonomous")

    def test_accepts_the_two_real_scopes(self):
        _manifest(scope=SkillScope.READ_ONLY)
        _manifest(scope=SkillScope.WRITE_WITH_APPROVAL)


class ExecuteSkillPermissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_denies_a_role_not_in_required_roles(self):
        manifest = _manifest(required_roles=["DONO"])
        record = AsyncMock()
        with (
            patch("app.skills.executor.get_manifest", new=AsyncMock(return_value=manifest)),
            patch("app.skills.executor.record_skill_execution", new=record),
        ):
            with self.assertRaises(SkillPermissionError):
                await execute_skill(
                    empresa_id=1, role="ESTOQUISTA", usuario_id=9, session_id="s1",
                    name="test_skill", version="1.0.0", inputs={"x": 1},
                )

        self.assertEqual(record.await_args.kwargs["status"], "denied")

    async def test_denies_when_no_manifest_resolves_for_this_tenant(self):
        """get_manifest itself is tenant-scoped in its query (app/skills/
        registry.py) — a name+version registered for a different empresa_id
        resolves to None here, not to that other tenant's document."""
        get_manifest = AsyncMock(return_value=None)
        record = AsyncMock()
        with (
            patch("app.skills.executor.get_manifest", new=get_manifest),
            patch("app.skills.executor.record_skill_execution", new=record),
        ):
            with self.assertRaises(SkillPermissionError):
                await execute_skill(
                    empresa_id=2, role="DONO", usuario_id=9, session_id="s1",
                    name="test_skill", version="1.0.0", inputs={"x": 1},
                )

        get_manifest.assert_awaited_once_with(2, "test_skill", "1.0.0")
        self.assertEqual(record.await_args.kwargs["status"], "denied")

    async def test_denies_an_inactive_skill(self):
        manifest = _manifest(active=False)
        with (
            patch("app.skills.executor.get_manifest", new=AsyncMock(return_value=manifest)),
            patch("app.skills.executor.record_skill_execution", new=AsyncMock()),
        ):
            with self.assertRaises(SkillPermissionError):
                await execute_skill(
                    empresa_id=1, role="DONO", usuario_id=9, session_id="s1",
                    name="test_skill", version="1.0.0", inputs={"x": 1},
                )

    async def test_a_registry_lookup_failure_denies_rather_than_crashing(self):
        with (
            patch("app.skills.executor.get_manifest", new=AsyncMock(side_effect=RuntimeError("mongo down"))),
            patch("app.skills.executor.record_skill_execution", new=AsyncMock()) as record,
        ):
            with self.assertRaises(SkillPermissionError):
                await execute_skill(
                    empresa_id=1, role="DONO", usuario_id=9, session_id="s1",
                    name="test_skill", version="1.0.0", inputs={"x": 1},
                )
        self.assertEqual(record.await_args.kwargs["status"], "denied")


class ExecuteSkillValidationTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_invalid_input_before_running_the_entrypoint(self):
        ran = AsyncMock(return_value={"y": 1})
        _register_fake_module(run=ran)
        self.addCleanup(_unregister_fake_module)
        manifest = _manifest()

        with (
            patch("app.skills.executor.get_manifest", new=AsyncMock(return_value=manifest)),
            patch("app.skills.executor.record_skill_execution", new=AsyncMock()) as record,
        ):
            with self.assertRaises(SkillValidationError):
                await execute_skill(
                    empresa_id=1, role="DONO", usuario_id=9, session_id="s1",
                    name="test_skill", version="1.0.0", inputs={},  # missing required "x"
                )

        ran.assert_not_awaited()  # never reached the entrypoint
        self.assertEqual(record.await_args.kwargs["status"], "invalid_input")

    async def test_rejects_an_output_that_fails_the_output_schema(self):
        _register_fake_module(run=AsyncMock(return_value={"wrong_key": 1}))
        self.addCleanup(_unregister_fake_module)
        manifest = _manifest()

        with (
            patch("app.skills.executor.get_manifest", new=AsyncMock(return_value=manifest)),
            patch("app.skills.executor.record_skill_execution", new=AsyncMock()) as record,
        ):
            with self.assertRaises(SkillValidationError):
                await execute_skill(
                    empresa_id=1, role="DONO", usuario_id=9, session_id="s1",
                    name="test_skill", version="1.0.0", inputs={"x": 1},
                )

        self.assertEqual(record.await_args.kwargs["status"], "invalid_output")

    async def test_an_entrypoint_outside_the_prefix_is_rejected_even_if_the_manifest_bypassed_validation(self):
        # SkillManifest's own validator already blocks this at
        # construction (SkillManifestContractTests above) — this proves
        # the executor's own check is real defense in depth, not the only
        # thing standing between a bad document and code execution.
        manifest = SkillManifest.model_construct(
            id="x", empresa_id=1, name="evil", version="1.0.0", description="d", owner="o",
            entrypoint="os.system:call", timeout_seconds=1.0, max_retries=0,
            scope=SkillScope.READ_ONLY, required_roles=["DONO"],
            input_schema={"type": "object"}, output_schema={"type": "object"}, tags=[], active=True,
        )
        with (
            patch("app.skills.executor.get_manifest", new=AsyncMock(return_value=manifest)),
            patch("app.skills.executor.record_skill_execution", new=AsyncMock()) as record,
        ):
            with self.assertRaises(SkillPermissionError):
                await execute_skill(
                    empresa_id=1, role="DONO", usuario_id=9, session_id="s1",
                    name="evil", version="1.0.0", inputs={},
                )
        self.assertEqual(record.await_args.kwargs["status"], "denied")


class ExecuteSkillRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_slow_skill_is_actually_interrupted_by_the_timeout(self):
        cancelled = {"value": False}

        async def slow(x):
            try:
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                cancelled["value"] = True
                raise
            return {"y": x}

        _register_fake_module(run=slow)
        self.addCleanup(_unregister_fake_module)
        manifest = _manifest(timeout_seconds=0.05)

        with (
            patch("app.skills.executor.get_manifest", new=AsyncMock(return_value=manifest)),
            patch("app.skills.executor.record_skill_execution", new=AsyncMock()) as record,
        ):
            started = time.perf_counter()
            with self.assertRaises(Exception):
                await execute_skill(
                    empresa_id=1, role="DONO", usuario_id=9, session_id="s1",
                    name="test_skill", version="1.0.0", inputs={"x": 1},
                )
            elapsed = time.perf_counter() - started

        self.assertLess(elapsed, 1.0)  # not the full 5s sleep
        self.assertTrue(cancelled["value"])  # the coroutine was really cancelled, not just abandoned
        self.assertEqual(record.await_args.kwargs["status"], "timeout")

    async def test_an_exception_from_the_entrypoint_is_recorded_as_error(self):
        _register_fake_module(run=AsyncMock(side_effect=RuntimeError("boom")))
        self.addCleanup(_unregister_fake_module)
        manifest = _manifest()

        with (
            patch("app.skills.executor.get_manifest", new=AsyncMock(return_value=manifest)),
            patch("app.skills.executor.record_skill_execution", new=AsyncMock()) as record,
        ):
            with self.assertRaises(Exception):
                await execute_skill(
                    empresa_id=1, role="DONO", usuario_id=9, session_id="s1",
                    name="test_skill", version="1.0.0", inputs={"x": 1},
                )

        self.assertEqual(record.await_args.kwargs["status"], "error")

    async def test_success_runs_the_entrypoint_and_records_success(self):
        ran = AsyncMock(return_value={"y": 42})
        _register_fake_module(run=ran)
        self.addCleanup(_unregister_fake_module)
        manifest = _manifest()

        with (
            patch("app.skills.executor.get_manifest", new=AsyncMock(return_value=manifest)),
            patch("app.skills.executor.record_skill_execution", new=AsyncMock()) as record,
        ):
            result = await execute_skill(
                empresa_id=1, role="DONO", usuario_id=9, session_id="s1",
                name="test_skill", version="1.0.0", inputs={"x": 1},
            )

        ran.assert_awaited_once_with(x=1)
        self.assertEqual(result, {"y": 42})
        self.assertEqual(record.await_args.kwargs["status"], "success")
        self.assertEqual(record.await_args.kwargs["outputs"], {"y": 42})

    async def test_a_failure_to_record_the_audit_does_not_hide_the_real_result(self):
        ran = AsyncMock(return_value={"y": 42})
        _register_fake_module(run=ran)
        self.addCleanup(_unregister_fake_module)
        manifest = _manifest()

        with (
            patch("app.skills.executor.get_manifest", new=AsyncMock(return_value=manifest)),
            patch("app.skills.executor.record_skill_execution", new=AsyncMock(side_effect=RuntimeError("mongo down"))),
        ):
            result = await execute_skill(
                empresa_id=1, role="DONO", usuario_id=9, session_id="s1",
                name="test_skill", version="1.0.0", inputs={"x": 1},
            )

        self.assertEqual(result, {"y": 42})  # the audit-write failure never surfaced here


class RegistryListActiveTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        registry_module._cache.clear()

    async def test_filters_tenant_and_role_in_the_query_itself(self):
        collection = AsyncMock()
        collection.find = lambda query: _AsyncIter([])
        db = types.SimpleNamespace(skill_registry=collection)
        captured = {}

        def fake_find(query):
            captured["query"] = query
            return _AsyncIter([])

        collection.find = fake_find

        with patch("app.skills.registry.get_mongo_db", return_value=db):
            await registry_module.list_active(7, "GERENTE")

        self.assertEqual(captured["query"], {"empresaId": 7, "active": True, "requiredRoles": "GERENTE"})

    async def test_caches_within_the_ttl(self):
        db = types.SimpleNamespace(skill_registry=types.SimpleNamespace(find=lambda q: _AsyncIter([_doc()])))
        call_count = {"n": 0}

        def counting_find(query):
            call_count["n"] += 1
            return _AsyncIter([_doc()])

        db.skill_registry.find = counting_find

        with patch("app.skills.registry.get_mongo_db", return_value=db):
            first = await registry_module.list_active(1, "DONO")
            second = await registry_module.list_active(1, "DONO")

        self.assertEqual(call_count["n"], 1)  # second call served from cache
        self.assertEqual(first, second)

    async def test_fails_closed_to_an_empty_list_on_a_mongo_error(self):
        db = types.SimpleNamespace(skill_registry=types.SimpleNamespace(
            find=lambda q: (_ for _ in ()).throw(RuntimeError("mongo down"))
        ))
        with patch("app.skills.registry.get_mongo_db", return_value=db):
            result = await registry_module.list_active(1, "DONO")

        self.assertEqual(result, [])  # not a hardcoded fallback, not a raised exception

    async def test_get_manifest_is_scoped_to_the_requesting_tenant(self):
        db = types.SimpleNamespace(skill_registry=AsyncMock())
        db.skill_registry.find_one = AsyncMock(return_value=None)

        with patch("app.skills.registry.get_mongo_db", return_value=db):
            result = await registry_module.get_manifest(2, "test_skill", "1.0.0")

        db.skill_registry.find_one.assert_awaited_once_with(
            {"empresaId": 2, "name": "test_skill", "version": "1.0.0"}
        )
        self.assertIsNone(result)


class _AsyncIter:
    def __init__(self, items):
        self._items = list(items)

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for item in self._items:
            yield item


def _doc():
    return {
        "_id": "abc123", "empresaId": 1, "name": "test_skill", "version": "1.0.0",
        "description": "d", "owner": "o", "entrypoint": f"{FAKE_MODULE_NAME}:run",
        "timeoutSeconds": 1.0, "maxRetries": 0, "scope": "read_only",
        "requiredRoles": ["DONO"], "inputSchema": {"type": "object"},
        "outputSchema": {"type": "object"}, "tags": [], "active": True,
    }


if __name__ == "__main__":
    unittest.main()
