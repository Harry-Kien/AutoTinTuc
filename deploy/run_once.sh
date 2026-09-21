#!/usr/bin/env bash
# One fetch/draft/post cycle. Linux equivalent of scripts/fastnews247_run_live.cmd.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# shellcheck disable=SC1091
[ -f .env ] && set -a && . ./.env && set +a

export PYTHONIOENCODING=utf-8
mkdir -p logs storage/fastnews247 outputs/fastnews247

exec python3 scripts/fastnews247_mvp.py --once --post >> logs/fastnews247_scheduler.log 2>&1
