"""
Support tools for the Vision Agent.
  - encode_image_base64: reads a file and converts it to base64
  - crosscheck_with_inventory: cross-checks detected products against Postgres stock
  - generate_shelf_report: generates formatted text of the analysis result

Note: generate_shelf_report()'s output is shown directly to the employee, so
it is deliberately kept in Portuguese, like the rest of the chat UX.
"""
from __future__ import annotations

import base64
from pathlib import Path

from app.tools.postgres_tools import get_shelf_inventory_crosscheck


def encode_image_base64(path: Path) -> str:
    """
    Reads an image from disk and returns base64.
    Supports jpg, png, webp, gif.
    """
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


async def crosscheck_with_inventory(
    empresa_id: int,
    store_id: int | None,
    detected_products: list[str],
) -> dict:
    """
    Cross-checks visually detected products against the PostgreSQL inventory.

    For each detected product, checks:
      - Whether it exists in the catalog (approximate name search)
      - Current stock vs minimum
      - Active store alerts, in a separate context

    Also returns products expected on the shelf that were NOT detected in the photo.
    """
    # The v6 schema relates company → retail_store → inventory → batch → product.
    # Alerts have no FK to product/batch, so missing items are inferred from
    # critical stock, not from the alert's free-text title/description.
    return await get_shelf_inventory_crosscheck(empresa_id, store_id, detected_products)


def generate_shelf_report(result: dict) -> str:
    """
    Generates a readable text report to show the employee
    after the visual shelf analysis.
    (Output kept in Portuguese — see module docstring.)
    """
    lines: list[str] = []

    estado = result.get("estado_geral", "?").upper()
    ocupacao = result.get("ocupacao_pct", 0)
    confianca = result.get("confianca_analise", 0)

    # Header
    if estado == "CRÍTICO" or estado == "CRITICO":
        lines.append("PRATELEIRA EM ESTADO CRÍTICO")
    elif estado == "ATENÇÃO" or estado == "ATENCAO":
        lines.append("Prateleira requer atenção")
    elif estado == "INVALIDO":
        lines.append("Imagem inválida — não parece ser uma prateleira.")
        return "\n".join(lines)
    else:
        lines.append("Prateleira em estado adequado")

    lines.append(f"Ocupação estimada: {ocupacao}% | Confiança da análise: {int(confianca * 100)}%")
    lines.append("")

    # Detected products
    produtos = result.get("produtos_detectados", [])
    if produtos:
        lines.append(f"Produtos identificados ({len(produtos)}):")
        for p in produtos:
            qtd = p.get("quantidade_estimada", "?")
            pos = p.get("posicao", "")
            obs = p.get("observacao", "")
            line = f"  • {p.get('nome', '?')} — {qtd} unid. ({pos})"
            if obs:
                line += f" | {obs}"
            lines.append(line)
    else:
        lines.append("Nenhum produto identificado na imagem.")

    # Empty slots
    slots = result.get("slots_vazios", {})
    if slots.get("total_estimado", 0) > 0:
        lines.append("")
        lines.append(f"Espaços vazios detectados: ~{slots['total_estimado']}")
        if slots.get("descricao"):
            lines.append(f"  {slots['descricao']}")

    # Inventory cross-check
    cruzamento = result.get("cruzamento_inventario", {})
    ausentes = cruzamento.get("ausentes_esperados", [])
    if ausentes:
        lines.append("")
        lines.append(f"Produtos com estoque crítico não visíveis na foto ({len(ausentes)}):")
        for a in ausentes:
            lines.append(
                f"  • {a.get('product_name', '?')} — {a.get('quantity', 0)} un. "
                f"(mínimo: {a.get('min_quantity', '?')}) [{a.get('stock_status', '?')}]"
            )

    alertas = cruzamento.get("alertas_ativos", [])
    if alertas:
        lines.append("")
        lines.append(f"Alertas operacionais ativos ({len(alertas)}):")
        for alerta in alertas:
            lines.append(
                f"  • {alerta.get('title', '?')} [{alerta.get('type', '?')}/"
                f"{alerta.get('priority', '?')}]"
            )

    inventario_ok = cruzamento.get("encontrados", [])
    criticos = [i for i in inventario_ok if i.get("status") in ("RUPTURA", "ABAIXO_MINIMO")]
    if criticos:
        lines.append("")
        lines.append("Produtos visíveis com estoque crítico no sistema:")
        for c in criticos:
            lines.append(
                f"  • {c.get('name', '?')} — {c.get('quantity', 0)} un. "
                f"(mínimo: {c.get('min_quantity', '?')}) [{c.get('status')}]"
            )

    # Suggested actions
    acoes = result.get("acoes_sugeridas", [])
    if acoes:
        lines.append("")
        lines.append("Ações recomendadas:")
        for i, acao in enumerate(acoes, 1):
            lines.append(f"  {i}. {acao}")

    return "\n".join(lines)
