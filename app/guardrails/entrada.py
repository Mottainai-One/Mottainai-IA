"""
Guardrail de ENTRADA — roda ANTES de qualquer agente.
Responsabilidades:
  1. Sanitização: detecta prompt injection, caracteres de controle
  2. Validação de payload
  3. Rate-limit por usuário (Redis sliding window)
  4. Rejeição de conteúdo proibido (regex básico)
"""
import re
from dataclasses import dataclass

from app.cache.rate_limit import check_rate_limit
from config.settings import get_settings

settings = get_settings()

# Padrões de prompt injection conhecidos
_INJECTION_PATTERNS = re.compile(
    r"(ignore (previous|all|prior) instructions?"
    r"|you are now"
    r"|new system prompt"
    r"|jailbreak"
    r"|act as (an? )?(unrestricted|evil|dan)"
    r"|disregard (all |your )?instructions?"
    r"|<\s*script[^>]*>"          # XSS básico
    r"|\bexec\s*\(|\beval\s*\("   # code injection
    r"|DROP\s+TABLE|DELETE\s+FROM|UPDATE\s+\w+\s+SET)",  # SQL injection
    re.IGNORECASE,
)

# Conteúdo sensível que não deve entrar no pipeline
_SENSITIVE_PATTERNS = re.compile(
    r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b"  # CPF
    r"|\b\d{4}\s?\d{4}\s?\d{4}\s?\d{4}\b",  # cartão de crédito
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
    Executa todas as verificações de entrada.
    Retorna GuardrailResult com allowed=True se tudo OK.
    """
    # 1. Validação de tamanho
    if not user_input or not user_input.strip():
        return GuardrailResult(allowed=False, reason="Mensagem vazia.")
    if len(user_input) > 4096:
        return GuardrailResult(allowed=False, reason="Mensagem muito longa (máx 4096 chars).")

    # 2. Detecção de prompt injection
    if _INJECTION_PATTERNS.search(user_input):
        return GuardrailResult(
            allowed=False,
            reason="Conteúdo bloqueado: possível tentativa de manipulação do sistema.",
        )

    # 3. Remoção de dado sensível (PII) — avisa mas não bloqueia
    sanitized = _SENSITIVE_PATTERNS.sub("[DADO_REMOVIDO]", user_input)

    # 4. Rate limit: janela deslizante atômica no Redis
    rate_limit = await check_rate_limit(empresa_id, user_id)
    if not rate_limit.allowed:
        return GuardrailResult(
            allowed=False,
            reason=f"Rate limit excedido ({settings.rate_limit_rpm} req/min). Tente em instantes.",
        )

    return GuardrailResult(allowed=True, sanitized_input=sanitized)
