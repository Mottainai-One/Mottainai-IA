#!/usr/bin/env python3
"""
Mottainai — Setup MongoDB.

Aplica scripts/mongo/schema.json: coleções, validadores $jsonSchema e índices.
Dados demonstrativos só são incluídos com --seed-demo.

Uso:
  python scripts/setup_mongo.py              # cria/atualiza o schema
  python scripts/setup_mongo.py --check      # só compara e falha se divergir
  python scripts/setup_mongo.py --seed-demo  # + dados de demonstração

Por que o schema mora num JSON e não neste arquivo: este script já tinha
divergido do banco real — criava 7 coleções sem validador nenhum, enquanto o
banco em uso tem 22 coleções, todas com $jsonSchema. Um ambiente novo montado
por aqui não reproduzia a produção, e a classe de bug que os validadores pegam
(documento fora do schema, aceito por qualquer teste mockado, rejeitado com 500
pelo banco real) já derrubou quatro funcionalidades. Com o schema declarado num
arquivo diffável, a divergência vira uma linha de diff em vez de uma surpresa —
e `--check` transforma isso em algo que o CI consegue verificar.
"""
import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Adiciona o root do projeto ao path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import OperationFailure

from app.config import get_settings

settings = get_settings()
MONGO_URI = settings.mongo_uri
MONGO_DB = settings.mongo_db


def utcnow():
    return datetime.now(timezone.utc)


SCHEMA_PATH = ROOT / "scripts" / "mongo" / "schema.json"
INDEX_OPTIONS_CONFLICT = 85


def load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _index_keys(spec: dict) -> list[tuple]:
    """JSON só tem listas; o driver exige tuplas em cada par (campo, direção)."""
    return [(field, direction) for field, direction in spec["key"]]


async def _apply_validator(db, name: str, definition: dict, existing: set[str]) -> str:
    """
    Cria a coleção com o validador, ou aplica o validador via collMod se ela já
    existir. `create` só aceita validador na criação, então atualizar um schema
    de coleção existente exige collMod — é o que faz este script funcionar tanto
    num banco vazio quanto num que já está em uso.
    """
    options = {k: v for k, v in definition.items() if k != "indexes"}
    if not options:
        return "sem validador"
    if name not in existing:
        await db.create_collection(name, **options)
        return "coleção criada"
    await db.command({"collMod": name, **options})
    return "validador atualizado"


async def _apply_indexes(db, name: str, indexes: dict) -> list[str]:
    notes = []
    for index_name, spec in indexes.items():
        for stale in spec.get("supersedes", []):
            try:
                await db[name].drop_index(stale)
                notes.append(f"índice obsoleto removido: {stale}")
            except Exception:
                pass  # nunca existiu, ou já foi removido numa execução anterior
        try:
            await db[name].create_index(
                _index_keys(spec), name=index_name, unique=spec.get("unique", False),
            )
        except OperationFailure as exc:
            if exc.code != INDEX_OPTIONS_CONFLICT:
                raise
            notes.append(f"{index_name}: já existe índice equivalente com outro nome — mantido")
    return notes


async def apply_schema(db, schema: dict) -> None:
    existing = set(await db.list_collection_names())
    for name, definition in schema.items():
        status = await _apply_validator(db, name, definition, existing)
        notes = await _apply_indexes(db, name, definition.get("indexes", {}))
        print(f"[mongo-setup] {name:24} {status}")
        for note in notes:
            print(f"[mongo-setup]     - {note}")


async def check_schema(db, schema: dict) -> list[str]:
    """
    Compara o banco com o schema declarado e devolve as divergências.

    Só reporta o que o schema exige e o banco não tem. Uma coleção ou índice a
    mais no banco não é erro — pode ser trabalho em andamento — mas um campo
    obrigatório ou índice ausente significa que este ambiente aceita documento
    que a produção rejeita, que é exatamente a divergência a evitar.
    """
    drift: list[str] = []
    existing = set(await db.list_collection_names())
    for name, definition in schema.items():
        if name not in existing:
            drift.append(f"coleção ausente: {name}")
            continue
        if "validator" in definition:
            live = None
            cursor = await db.list_collections(filter={"name": name})
            async for info in cursor:
                live = info.get("options", {}).get("validator")
            if live != definition["validator"]:
                drift.append(f"validador diferente: {name}")
        live_indexes = set(await db[name].index_information())
        for index_name in definition.get("indexes", {}):
            if index_name not in live_indexes:
                drift.append(f"índice ausente: {name}.{index_name}")
    return drift


async def setup(seed_demo: bool = False, check_only: bool = False):
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

    schema = load_schema()

    if check_only:
        drift = await check_schema(db, schema)
        if drift:
            print(f"[mongo-setup] {len(drift)} divergência(s) entre o banco e scripts/mongo/schema.json:")
            for item in drift:
                print(f"  - {item}")
            client.close()
            sys.exit(1)
        print(f"[mongo-setup] Banco em dia com o schema ({len(schema)} coleções).")
        client.close()
        return

    print(f"[mongo-setup] Aplicando schema ({len(schema)} coleções)...")
    await apply_schema(db, schema)


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
                "category": "POLITICA",
                "version": "1.0",
                "createdAt": utcnow(),
            },
            {
                "slug": "guia-vencimento-pereciveis",
                "empresaId": empresa_id,
                "title": "Guia de Controle de Perecíveis e Validade",
                "source": "manual_operacional",
                "category": "MANUAL",
                "version": "1.0",
                "createdAt": utcnow(),
            },
            {
                "slug": "procedimento-descarte-doacao",
                "empresaId": empresa_id,
                "title": "Procedimentos de Descarte e Doação",
                "source": "manual_operacional",
                "category": "PROCEDIMENTO",
                "version": "1.0",
                "createdAt": utcnow(),
            },
            {
                "slug": "faq-cliente-mottainai",
                "empresaId": empresa_id,
                "title": "FAQ — Perguntas Frequentes de Clientes",
                "source": "faq",
                "category": "FAQ",
                "version": "1.0",
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
    print(f"  Coleções: {len(load_schema())} (ver scripts/mongo/schema.json)")
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
    parser.add_argument(
        "--check",
        action="store_true",
        help="não altera nada: compara o banco com scripts/mongo/schema.json e sai com 1 se divergir",
    )
    args = parser.parse_args()
    asyncio.run(setup(seed_demo=args.seed_demo, check_only=args.check))
