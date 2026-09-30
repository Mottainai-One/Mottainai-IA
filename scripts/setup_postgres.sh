#!/usr/bin/env bash
# ============================================================
# Mottainai — PostgreSQL preflight
# Valida o schema operacional configurado sem criar, resetar ou carregar dados.
# Uso: bash scripts/setup_postgres.sh
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="$SCRIPT_DIR/.venv/bin/python"

log() { echo "[postgres-preflight] $*"; }

if [[ ! -x "$PYTHON" ]]; then
    log "ERRO: ambiente virtual não encontrado em $PYTHON"
    exit 1
fi

cd "$SCRIPT_DIR"
log "Validando conexão e schema operacional (somente leitura)..."

"$PYTHON" "$SCRIPT_DIR/scripts/preflight_postgres.py"

log "Instalação/migração é manual e deve usar o repositório operacional versionado."
