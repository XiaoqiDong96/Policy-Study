#!/usr/bin/env bash
set -euo pipefail

ROOT="${ROOT:-$HOME/nev_policy_project}"
DOMAIN_KEY="${DOMAIN_KEY:?DOMAIN_KEY is required}"
DOMAIN_LABEL="${DOMAIN_LABEL:-$DOMAIN_KEY}"
OUTPUT_ROOT="${OUTPUT_ROOT:-outputs/${DOMAIN_KEY}_policy_panel}"
INPUT="${INPUT:-${OUTPUT_ROOT}/stage2_dual_vote_boundary/qwen_full/final/${DOMAIN_KEY}_dual_vote_final_qwen_yes.jsonl}"
OUTPUT="${OUTPUT:-${OUTPUT_ROOT}/tool_refinement/${DOMAIN_KEY}_tool_refined.jsonl}"
SESSION="${SESSION:-${DOMAIN_KEY}_tool_refine}"
PARALLEL_DOCS="${PARALLEL_DOCS:-6}"

cd "$ROOT"
. .venv/bin/activate

mkdir -p "$(dirname "$OUTPUT")" logs

if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "[domain-tool] tmux session already running: $SESSION"
  tmux ls
  exit 0
fi

tmux new-session -d -s "$SESSION" \
  "cd '$ROOT' && . .venv/bin/activate && python scripts/domain_policy_tool_refiner.py \
    --domain-key '$DOMAIN_KEY' \
    --domain-label '$DOMAIN_LABEL' \
    --input-classified '$INPUT' \
    --output '$OUTPUT' \
    --model minimax-m2.5:cloud \
    --parallel-docs '$PARALLEL_DOCS' \
    --max-body-chars 8000 \
    --num-ctx 16384 \
    --llm-timeout 600 \
    --llm-retries 4 \
    --retry-base-sleep 8 \
    --progress-every 100 \
    2>&1 | tee -a 'logs/${SESSION}.tmux.log'"

tmux ls
