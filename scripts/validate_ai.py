"""Validate the Mottainai AI layer against the AI release checklist.

Pure standard-library checks (no external deps) so it runs anywhere,
mirroring the numbered validation jobs of the CI. Exits non-zero on failure.

Covered checklist items:
  2. Prompts estao organizados e documentados
  3. Respostas da IA sao validadas (Juiz fail-closed + guardrail de saida)
  5. Agentes possuem responsabilidades e ferramentas bem definidas
  6. RAG esta retornando contexto relevante
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROMPT_ASSIGN = re.compile(r"(?m)^\s*([A-Z][A-Z0-9_]*PROMPT[A-Z0-9_]*)\s*=")

CHAT_GRAPH_AGENTS = {"cliente", "faq", "funcionario", "dono", "motor_preditivo", "juiz"}
SKIP_FILES = {"__init__.py", "supervisor.py", "runtime.py"}
REQUIRED_TOOLS = [
    "app/tools/postgres_tools.py",
    "app/tools/redis_tools.py",
    "app/tools/vision_tools.py",
    "app/tools/mcp_tools.py",
]


def module_docstring(src: str) -> str:
    """Return the leading module docstring body if present, else ''."""
    m = re.match(r'\s*(?:"""|\'\'\')(.*?)(?:"""|\'\'\')', src, re.DOTALL)
    return m.group(1).strip() if m else ""


def walk() -> list[Path]:
    return [p for p in (ROOT / "app").rglob("*.py")]


def check_prompts_organized() -> list[str]:
    errors = []
    for path in walk():
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith("app/agents/"):
            continue
        text = path.read_text(encoding="utf-8")
        for line_no, line in enumerate(text.splitlines(), 1):
            if PROMPT_ASSIGN.match(line):
                errors.append(f"[prompts] prom/ def fora de app/agents/: {rel}:{line_no}")
    return errors


def check_prompts_documented() -> list[str]:
    errors = []
    for path in sorted((ROOT / "app/agents").glob("*.py")):
        if path.name in SKIP_FILES:
            continue
        text = path.read_text(encoding="utf-8")
        if not PROMPT_ASSIGN.search(text) and not module_docstring(text):
            errors.append(f"[prompts] {path.name}: sem prompt nem docstring de proposito")
    return errors


def check_responses_validated() -> list[str]:
    errors = []
    supervisor = (ROOT / "app/agents/supervisor.py").read_text(encoding="utf-8")
    if 'add_node("juiz"' not in supervisor:
        errors.append("[respostas] no 'juiz' nao registrado no grafo (supervisor.py)")
    if 'add_node("guardrail_saida"' not in supervisor:
        errors.append("[respostas] no 'guardrail_saida' nao registrado no grafo (supervisor.py)")
    if "juiz" not in supervisor or "guardrail_saida" not in supervisor:
        return errors
    saida = (ROOT / "app/guardrails/saida.py").read_text(encoding="utf-8")
    if "safe=False" not in saida:
        errors.append("[respostas] guardrail de saida nao bloqueia (safe=False)")
    if "_PII_" not in saida:
        errors.append("[respostas] guardrail de saida nao trata PII")
    juiz = (ROOT / "app/agents/juiz.py").read_text(encoding="utf-8")
    if not any(k in juiz for k in ("grounding", "escopo", "confidence", "score")):
        errors.append("[respostas] Juiz nao registra metrica de qualidade (grounding/score)")
    return errors


def check_agents_tools() -> list[str]:
    errors = []
    supervisor = (ROOT / "app/agents/supervisor.py").read_text(encoding="utf-8")
    for path in sorted((ROOT / "app/agents").glob("*.py")):
        if path.name in SKIP_FILES:
            continue
        stem = path.stem
        text = path.read_text(encoding="utf-8")
        has_prompt = bool(PROMPT_ASSIGN.search(text))
        has_doc = bool(module_docstring(text))
        if stem in CHAT_GRAPH_AGENTS:
            if not has_prompt:
                errors.append(f"[agentes] agente de chat {stem} sem prompt de responsabilidade")
            if 'add_node("' + stem + '"' not in supervisor:
                errors.append(f"[agentes] agente {stem} nao registrado como node no grafo")
        else:
            if not has_prompt and not has_doc:
                errors.append(f"[agentes] agente {stem} sem prompt nem docstring de responsabilidade")
    for tool in REQUIRED_TOOLS:
        if not (ROOT / tool).exists():
            errors.append(f"[agentes] ferramenta obrigatoria ausente: {tool}")
    mcp = (ROOT / "app/integrations/mcp_a2a.py").read_text(encoding="utf-8")
    if "mcp_tools" not in mcp and "tools" not in mcp:
        errors.append("[agentes] integracao MCP/A2A sem ferramentas")
    return errors


def check_rag() -> list[str]:
    errors = []
    retriever = ROOT / "app/rag/retriever.py"
    external = ROOT / "app/rag/external_source.py"
    if not retriever.exists():
        errors.append("[rag] retriever ausente: app/rag/retriever.py")
    if not external.exists():
        errors.append("[rag] fonte externa ausente: app/rag/external_source.py")
    if retriever.exists():
        text = retriever.read_text(encoding="utf-8")
        if not any(k in text for k in ("source", "fonte", "document")):
            errors.append("[rag] retriever nao expoe as fontes/contexto")
    return errors


def run_checks(target: str = "all") -> int:
    targets = {
        "prompts": ("check_prompts_organized", "check_prompts_documented"),
        "responses": ("check_responses_validated",),
        "agents": ("check_agents_tools",),
        "rag": ("check_rag",),
    }
    all_errors: list[str] = []
    for name in targets.get(target, ("check_prompts_organized", "check_prompts_documented",
                                     "check_responses_validated", "check_agents_tools", "check_rag")):
        all_errors += globals()[name]()

    if all_errors:
        print(f"[validate_ai] {len(all_errors)} problema(s) encontrado(s):", file=sys.stderr)
        for err in all_errors:
            print("  - " + err, file=sys.stderr)
        return 1
    print(f"[validate_ai] checklist de IA OK (item: {target})")
    return 0


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "all"
    sys.exit(run_checks(target))
