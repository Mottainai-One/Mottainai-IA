#!/usr/bin/env bash
# ============================================================
# Mottainai — start.sh
# Sobe todos os serviços e inicia a API FastAPI
# Uso: bash start.sh
# ============================================================
set -euo pipefail

BREW_PREFIX="${HOMEBREW_PREFIX:-$HOME/.homebrew}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$SCRIPT_DIR/.venv/bin"

export PATH="$BREW_PREFIX/bin:$PATH"

log()  { echo "🔧 $*"; }
ok()   { echo "✅ $*"; }
fail() { echo "❌ $*"; exit 1; }

echo ""
echo "=========================================="
echo "  Mottainai IA Layer — Startup"
echo "=========================================="
echo ""

# ──────────────────────────────────────
# 1. Redis
# ──────────────────────────────────────
log "Verificando Redis..."
if redis-cli ping &>/dev/null 2>&1 || \
   "$BREW_PREFIX/opt/redis/bin/redis-cli" ping &>/dev/null 2>&1; then
    ok "Redis já está rodando"
else
    log "Iniciando Redis..."
    brew services start redis &>/dev/null || \
        "$BREW_PREFIX/opt/redis/bin/redis-server" \
        "$BREW_PREFIX/etc/redis.conf" --daemonize yes
    sleep 2
    ok "Redis iniciado"
fi

# ──────────────────────────────────────
# 2. PostgreSQL
# ──────────────────────────────────────
log "Validando PostgreSQL configurado (somente leitura)..."
bash "$SCRIPT_DIR/scripts/setup_postgres.sh"
ok "PostgreSQL e schema operacional validados"

# ──────────────────────────────────────
# 3. MongoDB
# ──────────────────────────────────────
log "Verificando MongoDB..."
if mongosh --quiet --eval "db.runCommand('ping')" &>/dev/null 2>&1; then
    ok "MongoDB já está rodando"
else
    log "Iniciando MongoDB..."
    brew services start mongodb-community@7.0 &>/dev/null || true
    sleep 3

    if ! mongosh --quiet --eval "db.runCommand('ping')" &>/dev/null 2>&1; then
        fail "MongoDB não iniciou. Verifique: brew services list"
    fi
    ok "MongoDB iniciado"
fi

# ──────────────────────────────────────
# 4. Setup MongoDB (idempotente)
# ──────────────────────────────────────
log "Configurando MongoDB..."
"$VENV/python" "$SCRIPT_DIR/scripts/setup_mongo.py"

# ──────────────────────────────────────
# 5. Embeddings (idempotente)
# ──────────────────────────────────────
log "Verificando embeddings RAG..."
"$VENV/python" "$SCRIPT_DIR/scripts/generate_embeddings.py"

# ──────────────────────────────────────
# 6. FastAPI
# ──────────────────────────────────────
echo ""
echo "=========================================="
echo "  Iniciando API FastAPI..."
echo "  http://localhost:8000"
echo "  Docs: http://localhost:8000/docs"
echo "  Ctrl+C para parar"
echo "=========================================="
echo ""

cd "$SCRIPT_DIR"
"$VENV/uvicorn" interfaces.api.main:app --host 0.0.0.0 --port 8000 --reload
