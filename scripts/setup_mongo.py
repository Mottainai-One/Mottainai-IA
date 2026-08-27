#!/usr/bin/env python3
"""
Mottainai — Setup MongoDB.

Cria coleções e índices. Dados demonstrativos só são incluídos com --seed-demo.
Uso: python scripts/setup_mongo.py [--seed-demo]
"""
import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

# Adiciona o root do projeto ao path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from motor.motor_asyncio import AsyncIOMotorClient

from app.config import get_settings

settings = get_settings()
MONGO_URI = settings.mongo_uri
MONGO_DB = settings.mongo_db


def utcnow():
    return datetime.now(timezone.utc)


async def setup(seed_demo: bool = False):
    print("[mongo-setup] Conectando ao MongoDB...")
    client = AsyncIOMotorClient(MONGO_URI)
    db = client[MONGO_DB]

    try:
        await db.command("ping")
        print("[mongo-setup] MongoDB OK")
    except Exception:
        print("[mongo-setup] ERRO: não foi possível conectar ao MongoDB configurado.")
        print("[mongo-setup] Certifique-se que o MongoDB está rodando:")
        print("  Windows: .\\scripts\\windows\\Start-Mongo.ps1")
        sys.exit(1)

    # ──────────────────────────────────────────────
    # 1. Índices
    # ──────────────────────────────────────────────
    print("[mongo-setup] Criando índices...")

    await db.conversations.create_index("sessionId", unique=True)
    await db.conversations.create_index([("empresaId", 1), ("status", 1)])

    await db.messages.create_index([("conversationId", 1), ("createdAt", 1)])

    await db.memories.create_index([("empresaId", 1), ("usuarioId", 1)], unique=True)

    await db.metrics.create_index([("createdAt", -1)])
    await db.metrics.create_index([("agent", 1), ("createdAt", -1)])

    await db.agent_executions.create_index([("empresaId", 1), ("createdAt", -1)])
    await db.agent_executions.create_index([("agent", 1), ("status", 1)])

    await db.rag_documents.create_index([("empresaId", 1)])
    await db.rag_documents.create_index("slug", unique=True)

    await db.rag_chunks.create_index([("documentId", 1)])
    await db.rag_chunks.create_index([("documentId", 1), ("chunk", 1)])

    print("[mongo-setup] Índices criados!")

    # ──────────────────────────────────────────────
    # 2. Seed: documentos RAG
    # ──────────────────────────────────────────────
    existing_docs = await db.rag_documents.count_documents({})
    if not seed_demo:
        print("[mongo-setup] Seed demo não solicitado. Coleções e índices estão prontos.")
    elif existing_docs > 0:
        print(f"[mongo-setup] RAG já tem {existing_docs} documento(s). Pulando seed.")
    else:
        print("[mongo-setup] Inserindo documentos RAG...")

        empresa_id = 1
        docs = [
            {
                "slug": "politica-estoque-mottainai",
                "empresaId": empresa_id,
                "title": "Política de Gestão de Estoque — Mottainai",
                "source": "manual_operacional",
                "createdAt": utcnow(),
            },
            {
                "slug": "guia-vencimento-pereciveis",
                "empresaId": empresa_id,
                "title": "Guia de Controle de Perecíveis e Validade",
                "source": "manual_operacional",
                "createdAt": utcnow(),
            },
            {
                "slug": "procedimento-descarte-doacao",
                "empresaId": empresa_id,
                "title": "Procedimentos de Descarte e Doação",
                "source": "manual_operacional",
                "createdAt": utcnow(),
            },
            {
                "slug": "faq-cliente-mottainai",
                "empresaId": empresa_id,
                "title": "FAQ — Perguntas Frequentes de Clientes",
                "source": "faq",
                "createdAt": utcnow(),
            },
        ]
        result = await db.rag_documents.insert_many(docs)
        doc_ids = result.inserted_ids

        # Chunks de texto por documento (sem embeddings — serão gerados na primeira consulta)
        # Os embeddings são calculados lazy pelo retriever (sentence-transformers)
        chunks_by_doc = {
            0: [  # politica-estoque
                "O sistema Mottainai utiliza o método FEFO (First Expired, First Out) para controle de estoque. Produtos com data de vencimento mais próxima devem ser vendidos prioritariamente.",
                "O nível mínimo de estoque (ponto de pedido) é calculado com base na demanda média dos últimos 30 dias mais o estoque de segurança de 20%.",
                "Produtos com menos de 7 dias para vencimento devem receber alerta de prioridade CRITICAL automaticamente. Com menos de 14 dias, prioridade HIGH.",
                "Transferências entre lojas devem ser aprovadas pelo gerente e registradas no sistema antes da movimentação física.",
                "O inventário físico deve ser realizado mensalmente. Divergências superiores a 2% do valor total exigem apuração imediata.",
            ],
            1: [  # guia-vencimento
                "Leite UHT pode ser armazenado em temperatura ambiente até 6 meses. Após aberto, refrigerar e consumir em até 5 dias.",
                "Iogurtes e produtos lácteos refrigerados devem ser mantidos entre 1°C e 10°C. Alerta de vencimento deve ser emitido com 5 dias de antecedência.",
                "Pães e produtos de panificação têm vida útil curta (2-5 dias). Acionar promoção relâmpago quando restar 48h para o vencimento.",
                "Refrigerantes e bebidas não alcoólicas seladas têm validade de 6 a 12 meses. Verificar integridade da embalagem no recebimento.",
                "O método FIFO deve ser aplicado a produtos não perecíveis. Para perecíveis, sempre usar FEFO independentemente.",
            ],
            2: [  # descarte-doacao
                "Produtos vencidos ou com embalagem violada devem ser descartados conforme normas sanitárias. Registrar no sistema com motivo e quantidade.",
                "Doações de alimentos próximos ao vencimento (>2 dias de validade) podem ser realizadas para entidades cadastradas no sistema.",
                "O custo de descarte deve ser monitorado mensalmente. Meta: redução de 15% ao trimestre através de ações preditivas.",
                "Promoções relâmpago de até 40% de desconto são recomendadas para produtos com 2-5 dias de vencimento, antes de acionar descarte.",
                "Registro de descarte requer aprovação do supervisor e deve incluir: produto, lote, quantidade, motivo e foto (quando possível).",
            ],
            3: [  # faq-cliente
                "Posso devolver um produto comprado? Sim, em até 7 dias após a compra, mediante apresentação do cupom fiscal.",
                "Como funciona o programa de fidelidade? Cada compra acumula pontos que podem ser trocados por descontos nas próximas compras.",
                "Qual o horário de funcionamento das lojas Mottainai? As lojas funcionam de segunda a sábado das 8h às 22h e domingos das 9h às 20h.",
                "Como solicitar nota fiscal de empresa? Informe o CNPJ no momento do pagamento. A nota é emitida automaticamente.",
                "Como entrar em contato com o suporte? Pelo WhatsApp (11) 99999-0000 ou email contato@mottainai.com, disponível 24h.",
            ],
        }

        # Insere chunks
        all_chunks = []
        for doc_idx, (doc_id, chunk_texts) in enumerate(zip(doc_ids, chunks_by_doc.values())):
            for chunk_num, text in enumerate(chunk_texts):
                all_chunks.append({
                    "documentId": doc_id,
                    "chunk": chunk_num,
                    "text": text,
                    "embedding": None,  # gerado lazy pelo retriever
                    "createdAt": utcnow(),
                })

        await db.rag_chunks.insert_many(all_chunks)
        print(f"[mongo-setup] {len(docs)} documentos + {len(all_chunks)} chunks inseridos!")

    # ──────────────────────────────────────────────
    # 3. Seed: memória de longo prazo demo
    # ──────────────────────────────────────────────
    existing_mem = await db.memories.count_documents({})
    if seed_demo and existing_mem == 0:
        print("[mongo-setup] Inserindo memória demo...")
        await db.memories.insert_one({
            "empresaId": 1,
            "usuarioId": 1,
            "preferences": ["relatórios compactos", "alertas por prioridade"],
            "facts": ["Gerente da loja Paulista", "Responsável por perecíveis"],
            "lastAgent": None,
            "lastSkill": None,
            "updatedAt": utcnow(),
        })
        print("[mongo-setup] Memória demo criada!")
    elif not seed_demo:
        print("[mongo-setup] Memória demo não solicitada.")

    print("")
    print("=== MongoDB pronto! ===")
    print("  URI: configurada")
    print(f"  DB:  {MONGO_DB}")
    print("  Coleções: conversations, messages, memories, metrics,")
    print("            agent_executions, rag_documents, rag_chunks")
    print("")
    print("Próximo passo: python scripts/generate_embeddings.py")

    client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Prepara coleções e índices locais do MongoDB.")
    parser.add_argument(
        "--seed-demo",
        action="store_true",
        help="inclui documentos RAG e memória de demonstração em um MongoDB local vazio",
    )
    args = parser.parse_args()
    asyncio.run(setup(seed_demo=args.seed_demo))
