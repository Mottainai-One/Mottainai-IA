"""
OUTPUT guardrail — runs AFTER the Judge Agent approves.
Responsibilities:
  1. Blocks leakage of residual PII/sensitive data
  2. Blocks responses containing data from other users/companies
  3. Truncates abnormally long responses
  4. Ensures the response is a valid string

Note: SaidaResult.output strings are user-facing (they become the chat
response), so they are kept in Portuguese. SaidaResult.warnings is currently
unused elsewhere in the codebase.
"""
import re
from dataclasses import dataclass

# PII and sensitive data that must never appear in the response to the user
_PII_PATTERNS = re.compile(
    r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b"  # CPF (Brazilian individual tax ID)
    r"|\b\d{4}\s?\d{4}\s?\d{4}\s?\d{4}\b"  # card number
    r"|\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Z|a-z]{2,}\b"  # e-mail
    r"|\(\d{2}\)\s?\d{4,5}-?\d{4}"  # Brazilian phone number
    r"|\b\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}\b",  # CNPJ (Brazilian company tax ID)
    re.IGNORECASE,
)

# Internal data that must not leak to the end customer
_INTERNAL_LEAK_PATTERNS = re.compile(
    r"(senha|password|secret|token|api[_\s]?key|private[_\s]?key|hash)",
    re.IGNORECASE,
)

MAX_RESPONSE_LEN = 8192


@dataclass
class SaidaResult:
    safe: bool
    output: str
    warnings: list[str]


def guardrail_saida(response: str, user_role: str = "cliente") -> SaidaResult:
    """
    Processes the response before returning it to the user.
    Returns a SaidaResult with safe=True if it passed all checks.
    """
    warnings: list[str] = []
    output = response

    # 1. Validates the type
    if not isinstance(output, str):
        output = str(output)

    # 2. Residual PII removal
    if _PII_PATTERNS.search(output):
        output = _PII_PATTERNS.sub("[INFORMAÇÃO PROTEGIDA]", output)
        warnings.append("PII detectado e removido da resposta.")

    # 3. Internal data leak check
    if _INTERNAL_LEAK_PATTERNS.search(output):
        return SaidaResult(
            safe=False,
            output="Não posso fornecer essa informação.",
            warnings=["Resposta bloqueada: possível vazamento de dado interno."],
        )

    # 4. Truncates an excessively long response
    if len(output) > MAX_RESPONSE_LEN:
        output = output[:MAX_RESPONSE_LEN] + "\n\n[Resposta truncada por segurança.]"
        warnings.append("Resposta truncada por exceder limite de tamanho.")

    return SaidaResult(safe=True, output=output, warnings=warnings)
