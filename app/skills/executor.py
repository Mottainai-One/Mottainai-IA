"""
The only place a skill's entrypoint actually runs.

Every call goes through, in this exact order:
  1. resolve the manifest (app/skills/registry.get_manifest)
  2. authorize — tenant AND role, before the entrypoint is even imported
  3. validate `inputs` against the manifest's input_schema
  4. import the entrypoint (allowlisted prefix only) and run it under a
     timeout (asyncio.wait_for — nothing else here bounds how long a
     skill can hold the event loop)
  5. validate its return value against output_schema
  6. record to skill_executions — always, on every one of the outcomes
     above, not only on success

A denial does not distinguish "wrong role" from "wrong tenant" from
"doesn't exist" (SkillPermissionError's docstring) — the caller gets the
same message either way, and skill_executions.status is the only place
that carries which one it actually was, for whoever is allowed to look.
"""
import asyncio
import importlib
import logging
import time
from datetime import datetime, timezone

import jsonschema

from app.observability.skill_executions import record_skill_execution
from app.skills.base import (
    ENTRYPOINT_ALLOWED_PREFIX,
    SkillExecutionError,
    SkillPermissionError,
    SkillValidationError,
)
from app.skills.registry import get_manifest

logger = logging.getLogger(__name__)


async def execute_skill(
    *,
    empresa_id: int,
    role: str,
    usuario_id: int,
    session_id: str,
    name: str,
    version: str,
    inputs: dict,
    conversation_id: object = None,
) -> object:
    started_at = datetime.now(timezone.utc)
    started = time.perf_counter()

    async def _audit(status: str, *, outputs: object = None, error: str | None = None) -> None:
        # A failure to *record* an execution must never prevent the
        # skill's real result or error from reaching its caller — that
        # would make observability itself a new way for this to fail.
        try:
            await record_skill_execution(
                empresa_id=empresa_id, usuario_id=usuario_id, session_id=session_id,
                conversation_id=conversation_id, skill=name, version=version,
                status=status, inputs=inputs, outputs=outputs, error=error,
                latency=time.perf_counter() - started,
                started_at=started_at, finished_at=datetime.now(timezone.utc),
            )
        except Exception:
            logger.exception("failed to record skill_execution for %s v%s", name, version)

    try:
        manifest = await get_manifest(empresa_id, name, version)
    except Exception as exc:
        await _audit("denied", error=f"registry unavailable: {exc}")
        raise SkillPermissionError(f"Skill '{name}' v{version} is unavailable.") from exc

    if manifest is None or not manifest.active or role not in manifest.required_roles:
        await _audit("denied", error="permission denied")
        raise SkillPermissionError(f"Not authorized to run '{name}' v{version}.")

    try:
        jsonschema.validate(instance=inputs, schema=manifest.input_schema)
    except jsonschema.ValidationError as exc:
        await _audit("invalid_input", error=exc.message)
        raise SkillValidationError(f"Input for '{name}' v{version} failed validation: {exc.message}") from exc

    module_name, function_name = manifest.entrypoint.split(":")
    if not module_name.startswith(ENTRYPOINT_ALLOWED_PREFIX):
        # SkillManifest's own validator already rejects this shape at
        # construction — this only matters if that were ever bypassed
        # (e.g. a manifest built without going through the Pydantic
        # model). Defense in depth, not the primary guard.
        await _audit("denied", error=f"entrypoint outside {ENTRYPOINT_ALLOWED_PREFIX!r}")
        raise SkillPermissionError(f"Entrypoint '{manifest.entrypoint}' is not allowlisted.")

    try:
        module = importlib.import_module(module_name)
        function = getattr(module, function_name)
    except (ImportError, AttributeError) as exc:
        await _audit("error", error=f"entrypoint not resolvable: {exc}")
        raise SkillExecutionError(f"Entrypoint '{manifest.entrypoint}' could not be loaded.") from exc

    try:
        result = await asyncio.wait_for(function(**inputs), timeout=manifest.timeout_seconds)
    except asyncio.TimeoutError as exc:
        await _audit("timeout", error=f"exceeded {manifest.timeout_seconds}s")
        raise SkillExecutionError(f"'{name}' v{version} timed out after {manifest.timeout_seconds}s.") from exc
    except Exception as exc:
        await _audit("error", error=str(exc))
        raise SkillExecutionError(f"'{name}' v{version} raised: {exc}") from exc

    try:
        jsonschema.validate(instance=result, schema=manifest.output_schema)
    except jsonschema.ValidationError as exc:
        await _audit("invalid_output", error=exc.message)
        raise SkillValidationError(f"Output of '{name}' v{version} failed validation: {exc.message}") from exc

    await _audit("success", outputs=result)
    return result
