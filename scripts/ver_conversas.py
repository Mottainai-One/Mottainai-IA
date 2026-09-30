"""
Visualiza no terminal as conversas, mensagens e memória de um usuário no MongoDB.
Uso:
    python scripts\\ver_conversas.py --usuario-id 101 --empresa-id 1
    python scripts\\ver_conversas.py --usuario-id 101 --empresa-id 1 --limit 5
"""
import argparse
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import pymongo

from config.settings import get_settings


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--usuario-id", type=int, required=True)
    parser.add_argument("--empresa-id", type=int, required=True)
    parser.add_argument("--limit", type=int, default=3, help="Quantas conversas mostrar (mais recentes primeiro)")
    args = parser.parse_args()

    settings = get_settings()
    client = pymongo.MongoClient(settings.mongo_uri)
    db = client[settings.mongo_db]

    print(f"\n=== Memória de longo prazo (usuarioId={args.usuario_id}) ===")
    mem = db.memories.find_one({"empresaId": args.empresa_id, "usuarioId": args.usuario_id})
    if mem:
        print(f"  preferences: {mem.get('preferences')}")
        print(f"  facts: {mem.get('facts')}")
        print(f"  lastAgent: {mem.get('lastAgent')}  lastSkill: {mem.get('lastSkill')}")
        print(f"  updatedAt: {mem.get('updatedAt')}")
    else:
        print("  (nenhuma memória registrada ainda)")

    print(f"\n=== Últimas {args.limit} conversas ===")
    conversas = list(
        db.conversations.find({"empresaId": args.empresa_id, "usuarioId": args.usuario_id})
        .sort("lastInteraction", -1)
        .limit(args.limit)
    )
    if not conversas:
        print("  (nenhuma conversa encontrada)")

    for conv in conversas:
        print(f"\n--- Sessão {conv['sessionId']} | agente={conv.get('agent')} | status={conv.get('status')} ---")
        mensagens = db.messages.find({"conversationId": conv["_id"]}).sort("createdAt", 1)
        for msg in mensagens:
            quem = "Voce" if msg["role"] == "user" else f"IA ({msg.get('agent')})"
            print(f"  {quem}: {msg['content']}")
            if msg.get("sources"):
                print(f"    fontes: {msg['sources']}")

    client.close()


if __name__ == "__main__":
    main()
