"""
Tools de suporte ao Agente de Visão.
  - encode_image_base64: lê arquivo e converte para base64
  - crosscheck_with_inventory: cruza produtos detectados com estoque no Postgres
  - generate_shelf_report: gera texto formatado do resultado da análise
"""
from __future__ import annotations

import base64
from pathlib import Path

from app.tools.postgres_tools import get_shelf_inventory_crosscheck


def encode_image_base64(path: Path) -> str:
    """
    Lê imagem do disco e retorna base64.
    Suporta jpg, png, webp, gif.
    """
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


async def crosscheck_with_inventory(
    empresa_id: int,
    store_id: int | None,
    detected_products: list[str],
) -> dict:
    """
    Cruza os produtos detectados visualmente com o inventário do PostgreSQL.

    Para cada produto detectado, verifica:
      - Se existe cadastrado (busca por nome aproximado)
      - Estoque atual vs mínimo
      - Alertas ativos da loja, em contexto separado

    Também retorna produtos esperados na prateleira que NÃO foram detectados na foto.
    """
    # O schema v6 relaciona company → retail_store → inventory → batch → product.
    # Alertas não têm FK para produto/lote, então itens ausentes são inferidos pelo
    # estoque crítico, não pelo texto livre de title/description do alerta.
    return await get_shelf_inventory_crosscheck(empresa_id, store_id, detected_products)


def generate_shelf_report(result: dict) -> str:
    """
    Gera relatório textual legível para exibir ao funcionário
    após a análise visual da prateleira.
    """
    lines: list[str] = []

    estado = result.get("estado_geral", "?").upper()
    ocupacao = result.get("ocupacao_pct", 0)
    confianca = result.get("confianca_analise", 0)

    # Cabeçalho
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

    # Produtos detectados
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

    # Slots vazios
    slots = result.get("slots_vazios", {})
    if slots.get("total_estimado", 0) > 0:
        lines.append("")
        lines.append(f"Espaços vazios detectados: ~{slots['total_estimado']}")
        if slots.get("descricao"):
            lines.append(f"  {slots['descricao']}")

    # Cruzamento com inventário
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

    # Ações sugeridas
    acoes = result.get("acoes_sugeridas", [])
    if acoes:
        lines.append("")
        lines.append("Ações recomendadas:")
        for i, acao in enumerate(acoes, 1):
            lines.append(f"  {i}. {acao}")

    return "\n".join(lines)
