"""Local, conservative extraction of facts and preferences for long-term memory.

Note: the regex patterns below match Portuguese phrases (e.g. "prefiro",
"gosto de", "sou", "trabalho") because they run against the end user's
message, which is in Portuguese — they are not translated.
"""
import re

_MAX_MEMORY_LENGTH = 200
_SENSITIVE_PATTERN = re.compile(
    r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b|\b\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}\b|"
    r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b|"
    r"\b(senha|password|token|api[ _-]?key|segredo)\b",
    re.IGNORECASE,
)
_PATTERNS = (
    ("preference", re.compile(r"\b(?:eu )?(?:prefiro|gosto de|quero receber|me chame de)\s+(.+)", re.IGNORECASE)),
    ("fact", re.compile(r"\b(?:eu )?(?:sou|trabalho (?:na|no|em)|tenho uma?|minha loja (?:é|fica))\s+(.+)", re.IGNORECASE)),
)


def extract_memories(message: str) -> dict[str, list[str]]:
    """Extracts only explicit, short, non-sensitive statements from the user."""
    result = {"preferences": [], "facts": []}
    normalized = " ".join(message.strip().split())
    if not normalized or len(normalized) > 1000 or _SENSITIVE_PATTERN.search(normalized):
        return result

    for category, pattern in _PATTERNS:
        match = pattern.search(normalized)
        if not match:
            continue
        value = match.group(0).rstrip(".?! ")[:_MAX_MEMORY_LENGTH]
        if _SENSITIVE_PATTERN.search(value):
            continue
        key = "preferences" if category == "preference" else "facts"
        result[key].append(value)
    return result
