"""
INPUT guardrail — runs BEFORE any agent.
Responsibilities:
  1. Sanitization: detects prompt injection, control characters
  2. Payload validation
  3. Per-user rate limit (Redis sliding window)
  4. Rejection of forbidden content (basic regex)

Note: GuardrailResult.reason strings are user-facing — supervisor.py returns
them directly as the chat error/response — so they are kept in Portuguese.
"""
import re
from dataclasses import dataclass

from app.cache.rate_limit import check_rate_limit
from config.settings import get_settings

settings = get_settings()

# Known prompt injection patterns
_INJECTION_PATTERNS = re.compile(
    r"(ignore (previous|all|prior) instructions?"
    r"|you are now"
    r"|new system prompt"
    r"|jailbreak"
    r"|act as (an? )?(unrestricted|evil|dan)"
    r"|disregard (all |your )?instructions?"
    r"|<\s*script[^>]*>"          # basic XSS
    r"|\bexec\s*\(|\beval\s*\("   # code injection
    r"|DROP\s+TABLE|DELETE\s+FROM|UPDATE\s+\w+\s+SET)",  # SQL injection
    re.IGNORECASE,
)

# Sensitive content that must not enter the pipeline
_SENSITIVE_PATTERNS = re.compile(
    r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b"  # CPF (Brazilian tax ID)
    r"|\b\d{4}\s?\d{4}\s?\d{4}\s?\d{4}\b",  # credit card
    re.IGNORECASE,
)


@dataclass
class GuardrailResult:
    allowed: bool
    reason: str | None = None
    sanitized_input: str | None = None


async def guardrail_entrada(
    user_input: str,
    user_id: int,
    empresa_id: int,
) -> GuardrailResult:
    """
    Runs all input checks.
    Returns a GuardrailResult with allowed=True if everything is OK.
    """
    # 1. Length validation
    if not user_input or not user_input.strip():
        return GuardrailResult(allowed=False, reason="Mensagem vazia.")
    if len(user_input) > 4096:
        return GuardrailResult(allowed=False, reason="Mensagem muito longa (máx 4096 chars).")

    # 2. Prompt injection detection
    if _INJECTION_PATTERNS.search(user_input):
        return GuardrailResult(
            allowed=False,
            reason="Conteúdo bloqueado: possível tentativa de manipulação do sistema.",
        )

    # 3. Sensitive data (PII) removal — warns but does not block
    sanitized = _SENSITIVE_PATTERNS.sub("[DADO_REMOVIDO]", user_input)

    # 4. Rate limit: atomic sliding window in Redis
    rate_limit = await check_rate_limit(empresa_id, user_id)
    if not rate_limit.allowed:
        return GuardrailResult(
            allowed=False,
            reason=f"Rate limit excedido ({settings.rate_limit_rpm} req/min). Tente em instantes.",
        )

    return GuardrailResult(allowed=True, sanitized_input=sanitized)
