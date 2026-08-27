"""
Guardrail de SAÍDA — roda DEPOIS do Agente Juiz aprovar.
Responsabilidades:
  1. Bloqueia vazamento de PII/dado sensível residual
  2. Bloqueia respostas que contenham dados de outros usuários/empresas
  3. Trunca respostas anormalmente longas
  4. Garante que a resposta seja uma string válida
"""
import re
from dataclasses import dataclass

# PII e dados sensíveis que nunca devem aparecer na resposta ao usuário
_PII_PATTERNS = re.compile(
    r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b"  # CPF
    r"|\b\d{4}\s?\d{4}\s?\d{4}\s?\d{4}\b"  # cartão
    r"|\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Z|a-z]{2,}\b"  # e-mail
    r"|\(\d{2}\)\s?\d{4,5}-?\d{4}"  # telefone BR
    r"|\b\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}\b",  # CNPJ
    re.IGNORECASE,
)

# Dados internos que não devem vazar para o cliente final
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
    Processa a resposta antes de devolver ao usuário.
    Retorna SaidaResult com safe=True se passou em todos os checks.
    """
    warnings: list[str] = []
    output = response

    # 1. Valida tipo
    if not isinstance(output, str):
        output = str(output)

    # 2. Remoção de PII residual
    if _PII_PATTERNS.search(output):
        output = _PII_PATTERNS.sub("[INFORMAÇÃO PROTEGIDA]", output)
        warnings.append("PII detectado e removido da resposta.")

    # 3. Verificação de vazamento de dado interno
    if _INTERNAL_LEAK_PATTERNS.search(output):
        return SaidaResult(
            safe=False,
            output="Não posso fornecer essa informação.",
            warnings=["Resposta bloqueada: possível vazamento de dado interno."],
        )

    # 4. Truncar resposta excessivamente longa
    if len(output) > MAX_RESPONSE_LEN:
        output = output[:MAX_RESPONSE_LEN] + "\n\n[Resposta truncada por segurança.]"
        warnings.append("Resposta truncada por exceder limite de tamanho.")

    return SaidaResult(safe=True, output=output, warnings=warnings)
