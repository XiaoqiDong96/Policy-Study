#!/usr/bin/env bash
set -euo pipefail

ROOT="${ROOT:-$HOME/nev_policy_project}"
DOMAIN_KEY="${DOMAIN_KEY:?DOMAIN_KEY is required}"
PIPELINE_SCRIPT="${PIPELINE_SCRIPT:?PIPELINE_SCRIPT is required}"
CANDIDATES="${CANDIDATES:?CANDIDATES is required}"
OUTPUT_ROOT="${OUTPUT_ROOT:-outputs/${DOMAIN_KEY}_policy_panel}"
SESSION="${SESSION:-${DOMAIN_KEY}_stage1_minimax}"
OUTPUT_DIR="${OUTPUT_DIR:-${OUTPUT_ROOT}/stage1_minimax_adaptive}"
CANDIDATES_NAME="${CANDIDATES_NAME:-${DOMAIN_KEY}_stage1_candidates_norm.jsonl}"
CLASSIFIED_NAME="${CLASSIFIED_NAME:-${DOMAIN_KEY}_stage1_minimax.jsonl}"
PARALLEL_DOCS="${PARALLEL_DOCS:-4}"
MAX_PARALLEL_DOCS="${MAX_PARALLEL_DOCS:-6}"

cd "$ROOT"
. .venv/bin/activate

mkdir -p "$OUTPUT_DIR" logs

if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "[domain-stage1] tmux session already running: $SESSION"
  tmux ls
  exit 0
fi

tmux new-session -d -s "$SESSION" \
  "cd '$ROOT' && . .venv/bin/activate && python scripts/ollama_cloud_adaptive_runner.py \
    --pipeline-script '$PIPELINE_SCRIPT' \
    --candidates '$CANDIDATES' \
    --output-dir '$OUTPUT_DIR' \
    --candidates-name '$CANDIDATES_NAME' \
    --classified-name '$CLASSIFIED_NAME' \
    --model minimax-m2.5:cloud \
    --initial-parallel-docs '$PARALLEL_DOCS' \
    --min-parallel-docs 2 \
    --max-parallel-docs '$MAX_PARALLEL_DOCS' \
    --chunk-size 1000 \
    --prompt-mode standard \
    --ollama-format auto \
    --max-body-chars 8000 \
    --long-doc-mode evidence_pack \
    --num-ctx 16384 \
    --llm-timeout 600 \
    --llm-retries 4 \
    --retry-base-sleep 5 \
    --progress-every 100 \
    --weekly-limit-action stop \
    --session-limit-action stop \
    --short-cooldown-seconds 60 \
    --session-cooldown-seconds 60 \
    --weekly-cooldown-seconds 604800 \
    --rate-limit-threshold 3 \
    --increase-after-clean-chunks 2 \
    --proactive-session-break-minutes 0 \
    2>&1 | tee -a 'logs/${SESSION}.tmux.log'"

tmux ls
