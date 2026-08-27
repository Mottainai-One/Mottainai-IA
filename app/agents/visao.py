"""
Agente de Visão — Análise de Prateleira.
O funcionário tira foto da prateleira, este agente identifica:
  - Quais produtos estão presentes (por nome/embalagem)
  - Estimativa de quantidade por produto
  - Slots vazios (ruptura ou baixo estoque)
  - Comparação com o planograma esperado (via PostgreSQL)
  - Ação sugerida: repor / reposicionar / nenhuma

LLM: Google Gemini 1.5 Flash (gratuito, suporte nativo a imagens).
O resultado alimenta o Agente Funcionário e pode acionar alertas no Motor Preditivo.
"""
from __future__ import annotations

import base64
import json
import mimetypes
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage
from langchain_google_genai import ChatGoogleGenerativeAI

from app.config import get_settings
from app.tools.vision_tools import (
    crosscheck_with_inventory,
    encode_image_base64,
    generate_shelf_report,
)

settings = get_settings()

VISION_SYSTEM_PROMPT = """Você é o Agente de Visão do Mottainai, especialista em análise visual de prateleiras de varejo.

Analise a imagem da prateleira e responda em JSON com a seguinte estrutura EXATA:

{
  "produtos_detectados": [
    {
      "nome": "Nome do produto detectado",
      "quantidade_estimada": 0,
      "posicao": "esquerda|centro|direita|prateleira_superior|prateleira_media|prateleira_inferior",
      "visibilidade": "boa|parcial|ruim",
      "observacao": "texto livre opcional"
    }
  ],
  "slots_vazios": {
    "total_estimado": 0,
    "descricao": "descrição dos espaços vazios"
  },
  "ocupacao_pct": 0,
  "estado_geral": "adequado|atenção|crítico",
  "acoes_sugeridas": [
    "descrição da ação 1",
    "descrição da ação 2"
  ],
  "confianca_analise": 0.0
}

Regras:
- quantidade_estimada: número de unidades visíveis (não embalagens parcialmente ocultas).
- ocupacao_pct: % estimada da prateleira com produto (0 = vazia, 100 = cheia).
- estado_geral: "crítico" se ocupação < 30% ou produto principal ausente.
- confianca_analise: 0.0 a 1.0 (sua certeza sobre o que está visível).
- Se a imagem não for de uma prateleira, responda com estado_geral="inválido".
"""


def get_vision_llm() -> ChatGoogleGenerativeAI:
    return ChatGoogleGenerativeAI(
        model=settings.gemini_vision_model,
        google_api_key=settings.gemini_api_key,
        temperature=0.1,
    )


async def analyze_shelf(
    image_path: str | None = None,
    image_bytes: bytes | None = None,
    image_mime_type: str | None = None,
    empresa_id: int = 1,
    usuario_id: int | None = None,
    store_id: int | None = None,
    session_id: str | None = None,
    conversation_id: Any | None = None,
) -> dict:
    """
    Analisa uma foto de prateleira.

    Aceita:
      - image_path: caminho para arquivo local (jpg/png/webp)
      - image_bytes: bytes da imagem (upload via API)

    Retorna resultado da análise + cruzamento com inventário do Postgres.
    """
    # 1. Codifica a imagem em base64
    if image_path:
        b64 = encode_image_base64(Path(image_path))
    elif image_bytes:
        b64 = base64.b64encode(image_bytes).decode("utf-8")
    else:
        raise ValueError("Forneça image_path ou image_bytes.")

    # Preserva o MIME validado na borda HTTP; para uso local, infere pelo arquivo.
    if image_mime_type:
        mime = image_mime_type.split(";", 1)[0].strip().lower()
    elif image_path:
        mime = mimetypes.guess_type(image_path)[0] or "image/jpeg"
    else:
        mime = "image/jpeg"

    # 2. Chama Gemini Vision
    llm = get_vision_llm()

    message = HumanMessage(
        content=[
            {"type": "text", "text": VISION_SYSTEM_PROMPT},
            {
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"},
            },
        ]
    )

    response = await llm.ainvoke([message])
    raw = response.content.strip()

    # 3. Extrai JSON da resposta
    vision_result = _extract_json(raw)

    # 4. Cruza com inventário do Postgres, inclusive para uma prateleira vazia.
    raw_products = vision_result.get("produtos_detectados")
    detected_products = [
        product["nome"].strip()
        for product in raw_products
        if isinstance(product, dict)
        and isinstance(product.get("nome"), str)
        and product["nome"].strip()
    ] if isinstance(raw_products, list) else []
    if empresa_id:
        inventory_check = await crosscheck_with_inventory(
            empresa_id=empresa_id,
            store_id=store_id,
            detected_products=detected_products,
        )
        vision_result["cruzamento_inventario"] = inventory_check

    # 5. Gera relatório legível
    vision_result["relatorio_texto"] = generate_shelf_report(vision_result)

    # 6. Persiste no MongoDB para rastreabilidade
    if session_id:
        if usuario_id is None or conversation_id is None:
            raise ValueError("Contexto autenticado da sessão é obrigatório para persistir a análise.")
        from app.database.mongo import get_mongo_db
        from datetime import datetime, timezone
        db = get_mongo_db()
        await db.ai_results.insert_one({
            "conversationId": conversation_id,
            "sessionId": session_id,
            "empresaId": empresa_id,
            "usuarioId": usuario_id,
            "agent": "visao",
            "skill": "analise_prateleira",
            "type": "analysis",
            "result": vision_result,
            "createdAt": datetime.now(timezone.utc),
        })

    return vision_result


def _extract_json(raw: str) -> dict:
    """Extrai JSON da resposta do LLM (remove markdown se necessário)."""
    try:
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw.strip())
    except (json.JSONDecodeError, IndexError):
        # Fallback: retorna resposta bruta como texto
        return {
            "produtos_detectados": [],
            "slots_vazios": {"total_estimado": 0, "descricao": ""},
            "ocupacao_pct": 0,
            "estado_geral": "erro_parse",
            "acoes_sugeridas": [],
            "confianca_analise": 0.0,
            "resposta_raw": raw,
        }
