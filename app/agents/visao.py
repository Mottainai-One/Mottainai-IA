"""
Vision Agent — Shelf Analysis.
The employee takes a photo of the shelf, this agent identifies:
  - Which products are present (by name/packaging)
  - Estimated quantity per product
  - Empty slots (stockout or low stock)
  - Comparison against the expected planogram (via PostgreSQL)
  - Suggested action: restock / reposition / none

LLM: Google Gemini (free tier, native image support).
The result feeds the Employee Agent and can trigger alerts in the Predictive Engine.

Note: VISION_SYSTEM_PROMPT is deliberately kept in Portuguese. It also
defines the JSON schema the LLM must return (produtos_detectados,
quantidade_estimada, etc.) — those keys are a functional data contract with
the code below, not just prompt wording, so they are not translated either.
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
    Analyzes a shelf photo.

    Accepts:
      - image_path: path to a local file (jpg/png/webp)
      - image_bytes: image bytes (upload via API)

    Returns the analysis result + cross-check against Postgres inventory.
    """
    # 1. Encodes the image as base64
    if image_path:
        b64 = encode_image_base64(Path(image_path))
    elif image_bytes:
        b64 = base64.b64encode(image_bytes).decode("utf-8")
    else:
        raise ValueError("Provide image_path or image_bytes.")

    # Preserves the MIME type validated at the HTTP boundary; for local use, infers it from the file.
    if image_mime_type:
        mime = image_mime_type.split(";", 1)[0].strip().lower()
    elif image_path:
        mime = mimetypes.guess_type(image_path)[0] or "image/jpeg"
    else:
        mime = "image/jpeg"

    # 2. Calls Gemini Vision
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

    # 3. Extracts JSON from the response
    vision_result = _extract_json(raw)

    # 4. Cross-checks against Postgres inventory, including for an empty shelf.
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

    # 5. Generates a readable report
    vision_result["relatorio_texto"] = generate_shelf_report(vision_result)

    # 6. Persists to MongoDB for traceability
    if session_id:
        if usuario_id is None or conversation_id is None:
            raise ValueError("An authenticated session context is required to persist the analysis.")
        from datetime import datetime, timezone

        from app.database.mongo import get_mongo_db
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
    """Extracts JSON from the LLM response (strips markdown if needed)."""
    try:
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw.strip())
    except (json.JSONDecodeError, IndexError):
        # Fallback: returns the raw response as text
        return {
            "produtos_detectados": [],
            "slots_vazios": {"total_estimado": 0, "descricao": ""},
            "ocupacao_pct": 0,
            "estado_geral": "erro_parse",
            "acoes_sugeridas": [],
            "confianca_analise": 0.0,
            "resposta_raw": raw,
        }
