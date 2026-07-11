#!/usr/bin/env bash
set -euo pipefail

ROOT="${ROOT:-$HOME/nev_policy_project}"
PORT="${PORT:-8788}"
SESSION="${SESSION:-future_lowalt_status_dashboard}"

cd "$ROOT"
. .venv/bin/activate

mkdir -p logs

if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "[status] tmux session already running: $SESSION"
  tmux ls
  exit 0
fi

tmux new-session -d -s "$SESSION" \
  "cd '$ROOT' && . .venv/bin/activate && python scripts/domain_workflow_status_server.py --port '$PORT' 2>&1 | tee -a logs/${SESSION}.log"

tmux ls
