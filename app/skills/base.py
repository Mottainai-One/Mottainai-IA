"""
The skill contract: a versioned, tenant-scoped, auditable unit of
capability an agent can invoke, backed by the skill_registry collection
instead of a static Python registry — a document can be added, disabled
or re-pointed at a new entrypoint without a redeploy, and every call
through app/skills/executor.py is authorized, schema-checked and audited
the same way regardless of which skill it is.

Deliberately no `write_autonomous` scope: a skill may only read
(`read_only`) or write behind a separate human approval step
(`write_with_approval`, app/skills/approval.py — a later PR). A skill
that writes on its own recognition, with no human in the loop, is not a
value this contract accepts — not an oversight to fix later.
"""
from __future__ import annotations

import re
from enum import Enum

from pydantic import BaseModel, Field, field_validator

# Only entrypoints under this prefix may ever be imported by the executor.
# A skill_registry document is data written by whoever administers skills
# for a company; importing an arbitrary dotted path from that data would be
# remote code execution the moment one document is wrong or malicious.
ENTRYPOINT_ALLOWED_PREFIX = "app.skills."

_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
_ENTRYPOINT_RE = re.compile(r"^[\w.]+:[\w]+$")


class SkillScope(str, Enum):
    READ_ONLY = "read_only"
    WRITE_WITH_APPROVAL = "write_with_approval"


class SkillManifest(BaseModel):
    """Mirrors one skill_registry document. `id` and `empresa_id` are
    always present on anything the registry returns — they're optional
    here only so a manifest can be constructed before insertion (e.g. by
    a seed script) without a Mongo _id yet."""

    id: str | None = Field(default=None, description="str(_id) once persisted.")
    empresa_id: int
    name: str = Field(min_length=1, max_length=100)
    version: str
    description: str
    owner: str
    entrypoint: str = Field(description="'module.path:function_name', module under app.skills.")
    timeout_seconds: float = Field(gt=0, le=60)
    max_retries: int = Field(ge=0, le=5, default=0)
    scope: SkillScope
    required_roles: list[str] = Field(min_length=1)
    input_schema: dict
    output_schema: dict
    tags: list[str] = Field(default_factory=list)
    active: bool = True

    model_config = {"extra": "forbid"}

    @field_validator("version")
    @classmethod
    def _version_is_semver(cls, value: str) -> str:
        if not _SEMVER_RE.match(value):
            raise ValueError(f"version must be semver (X.Y.Z), got {value!r}")
        return value

    @field_validator("entrypoint")
    @classmethod
    def _entrypoint_is_shaped_and_allowlisted(cls, value: str) -> str:
        if not _ENTRYPOINT_RE.match(value):
            raise ValueError(f"entrypoint must look like 'module.path:function', got {value!r}")
        if not value.startswith(ENTRYPOINT_ALLOWED_PREFIX):
            raise ValueError(f"entrypoint must be under {ENTRYPOINT_ALLOWED_PREFIX!r}, got {value!r}")
        return value


class SkillPermissionError(Exception):
    """The caller's role or tenant does not match the resolved manifest,
    or no active manifest was found at all — these are deliberately not
    distinguished to the caller (see app/skills/executor.py): a 403 that
    reveals "the skill exists but you're the wrong tenant" leaks that the
    name/version is registered somewhere."""


class SkillValidationError(Exception):
    """Input or output failed the manifest's JSON Schema."""


class SkillExecutionError(Exception):
    """The entrypoint itself raised, or the call exceeded timeout_seconds."""
