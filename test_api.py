#!/usr/bin/env python3
"""
Script de teste rápido dos endpoints do Mottainai IA Layer.
Rode com: python test_api.py

Pré-requisitos:
  1. API rodando
  2. MOTTAINAI_JWT com token local gerado por scripts/generate_dev_token.py
"""
import asyncio
import httpx
import os

BASE = "http://localhost:8000"
TOKEN = os.environ.get("MOTTAINAI_JWT", "")


def auth_headers() -> dict[str, str]:
    if not TOKEN:
        raise RuntimeError("Defina MOTTAINAI_JWT com um token gerado por scripts/generate_dev_token.py.")
    return {"Authorization": f"Bearer {TOKEN}"}


async def test_health():
    async with httpx.AsyncClient() as c:
        r = await c.get(f"{BASE}/health")
        print(f"[health]  {r.status_code} → {r.json()}")


async def test_chat():
    payload = {
        "message": "Quais promoções estão ativas?",
        "session_id": "test-session-001",
    }
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"{BASE}/chat", json=payload, headers=auth_headers())
        data = r.json()
        print(f"[chat]    {r.status_code} | agent={data.get('agent')} | "
              f"judge={data.get('judge_score', 0):.2f} | "
              f"latency={data.get('latency_s', 0):.2f}s")
        print(f"          → {data.get('response', '')[:120]}...")


async def test_chat_funcionario():
    payload = {
        "message": "Quais alertas de estoque estão ativos?",
        "session_id": "test-func-001",
    }
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"{BASE}/chat", json=payload, headers=auth_headers())
        data = r.json()
        print(f"[func]    {r.status_code} | agent={data.get('agent')} | "
              f"judge={data.get('judge_score', 0):.2f} | "
              f"latency={data.get('latency_s', 0):.2f}s")
        print(f"          → {data.get('response', '')[:120]}...")


async def test_shelf(image_path: str):
    """Testa análise visual de prateleira. Passe o caminho de uma foto JPG."""
    async with httpx.AsyncClient(timeout=30) as c:
        with open(image_path, "rb") as f:
            files = {"image": (image_path, f, "image/jpeg")}
            data = {"store_id": "1"}
            r = await c.post(f"{BASE}/shelf/analyze", files=files, data=data, headers=auth_headers())
        result = r.json()
        print(f"[shelf]   {r.status_code} | estado={result.get('estado_geral')} | "
              f"ocupacao={result.get('ocupacao_pct')}%")
        print(f"          → {result.get('relatorio_texto', '')[:300]}")


async def test_metrics():
    async with httpx.AsyncClient() as c:
        r = await c.get(f"{BASE}/metrics/summary?days=7", headers=auth_headers())
        data = r.json().get("data", {})
        print(f"[metrics] {r.status_code} | requests={data.get('total_requests')} | "
              f"erros={data.get('qualidade', {}).get('indice_erros_pct')}%")


async def main():
    print("=" * 60)
    print("Mottainai IA — Testes de API")
    print("=" * 60)

    try:
        await test_health()
        await test_chat()
        await test_chat_funcionario()
        await test_metrics()
        # Para testar a visão, descomente e passe uma foto:
        # await test_shelf("foto_prateleira.jpg")
    except httpx.ConnectError:
        print("\n⚠  API não está rodando.")
        print("   Inicie com: .venv/bin/uvicorn app.main:app --reload")

    print("=" * 60)


asyncio.run(main())
