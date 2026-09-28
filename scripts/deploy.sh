#!/bin/sh
# Überträgt den aktuellen Commit per SSH auf einen Server und startet den Container.
# Aufruf: scripts/deploy.sh [nutzer@host] [zielpfad]
set -eu
HOST="${1:-daniel@192.168.178.20}"
DIR="${2:-/home/daniel/memomoji}"

cd "$(dirname "$0")/.."
echo "→ Übertrage $(git rev-parse --short HEAD) nach $HOST:$DIR"
git archive --format=tar HEAD | ssh "$HOST" "mkdir -p '$DIR' && tar -x -C '$DIR'"

ssh "$HOST" "cd '$DIR' && { [ -f .env ] || { cp .env.example .env; echo '! .env angelegt, bitte MEMOMOJI_ADMIN_PASSWORD eintragen'; }; } && docker compose up -d --build && docker compose ps"
