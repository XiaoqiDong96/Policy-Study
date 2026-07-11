#!/usr/bin/env bash
set -euo pipefail

ROOT="${ROOT:-$HOME/nev_policy_project}"
SESSION="${SESSION:-culture_industry_workflow}"

cd "$ROOT"

if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "[culture] tmux session already running: $SESSION"
  tmux ls
  exit 0
fi

tmux new-session -d -s "$SESSION" \
  "cd '$ROOT' && \
   DOMAIN_KEY=culture_industry \
   DOMAIN_LABEL='文化产业' \
   PIPELINE_SCRIPT=scripts/culture_industry_policy_pipeline.py \
   CANDIDATES=outputs/policy_packages_culture/culture_industry/candidates.jsonl \
   OUTPUT_ROOT=outputs/culture_industry_policy_panel \
   bash outputs/cloud_runbooks/run_culture_industry_full_workflow_orchestrator.sh"

tmux ls
