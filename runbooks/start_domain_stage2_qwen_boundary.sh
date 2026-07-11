#!/usr/bin/env bash
set -euo pipefail

ROOT="${ROOT:-$HOME/nev_policy_project}"
DOMAIN_KEY="${DOMAIN_KEY:?DOMAIN_KEY is required}"
DOMAIN_LABEL="${DOMAIN_LABEL:-$DOMAIN_KEY}"
PIPELINE_SCRIPT="${PIPELINE_SCRIPT:?PIPELINE_SCRIPT is required}"
OUTPUT_ROOT="${OUTPUT_ROOT:-outputs/${DOMAIN_KEY}_policy_panel}"
STAGE1="${STAGE1:-${OUTPUT_ROOT}/stage1_minimax_adaptive/${DOMAIN_KEY}_stage1_minimax.jsonl}"
BOUNDARY_DIR="${BOUNDARY_DIR:-${OUTPUT_ROOT}/stage2_dual_vote_boundary/qwen_full}"
BOUNDARY_FILE="${BOUNDARY_FILE:-$BOUNDARY_DIR/boundary_0p2_0p8_candidates.jsonl}"
BOUNDARY_SUMMARY="${BOUNDARY_SUMMARY:-$BOUNDARY_DIR/boundary_0p2_0p8_summary.json}"
SECOND_DIR="${SECOND_DIR:-$BOUNDARY_DIR/second_vote}"
SECOND_FILE="${SECOND_FILE:-$SECOND_DIR/qwen_boundary_full.jsonl}"
MERGED_DIR="${MERGED_DIR:-$BOUNDARY_DIR/merged}"
FINAL_DIR="${FINAL_DIR:-$BOUNDARY_DIR/final}"
SECOND_SESSION="${SECOND_SESSION:-${DOMAIN_KEY}_stage2_qwen}"
FINALIZE_SESSION="${FINALIZE_SESSION:-${DOMAIN_KEY}_stage2_finalize}"

cd "$ROOT"
. .venv/bin/activate

mkdir -p "$BOUNDARY_DIR" "$SECOND_DIR" "$MERGED_DIR" "$FINAL_DIR" logs

python scripts/select_domain_boundary_candidates.py \
  --domain-key "$DOMAIN_KEY" \
  --input-classified "$STAGE1" \
  --output "$BOUNDARY_FILE" \
  --summary "$BOUNDARY_SUMMARY" \
  --low 0.2 \
  --high 0.8

BOUNDARY_TOTAL="$(wc -l < "$BOUNDARY_FILE" | tr -d ' ')"
if [[ "$BOUNDARY_TOTAL" == "0" ]]; then
  : > "$MERGED_DIR/${DOMAIN_KEY}_minimax_qwen_boundary_0p2_0p8.jsonl"
  python scripts/build_domain_dual_vote_final_full.py \
    --domain-key "$DOMAIN_KEY" \
    --domain-label "$DOMAIN_LABEL" \
    --stage1 "$STAGE1" \
    --boundary-merged "$MERGED_DIR/${DOMAIN_KEY}_minimax_qwen_boundary_0p2_0p8.jsonl" \
    --output-dir "$FINAL_DIR" \
    --prefix "${DOMAIN_KEY}_dual_vote_final_qwen" \
    --export-split-txt
  echo "[domain-stage2] no boundary rows; final file built from Stage 1 high-confidence rows."
  exit 0
fi

if tmux has-session -t "$SECOND_SESSION" 2>/dev/null; then
  tmux kill-session -t "$SECOND_SESSION"
fi
if tmux has-session -t "$FINALIZE_SESSION" 2>/dev/null; then
  tmux kill-session -t "$FINALIZE_SESSION"
fi

tmux new-session -d -s "$SECOND_SESSION" \
  "cd '$ROOT' && . .venv/bin/activate && python scripts/ollama_cloud_adaptive_runner.py \
    --pipeline-script '$PIPELINE_SCRIPT' \
    --candidates '$BOUNDARY_FILE' \
    --output-dir '$SECOND_DIR' \
    --candidates-name qwen_boundary_candidates_norm.jsonl \
    --classified-name qwen_boundary_full.jsonl \
    --model qwen3.5:cloud \
    --initial-parallel-docs 4 \
    --min-parallel-docs 2 \
    --max-parallel-docs 6 \
    --chunk-size 500 \
    --prompt-mode standard \
    --ollama-format auto \
    --max-body-chars 8000 \
    --long-doc-mode evidence_pack \
    --num-ctx 16384 \
    --llm-timeout 900 \
    --llm-retries 4 \
    --retry-base-sleep 8 \
    --progress-every 50 \
    --weekly-limit-action stop \
    --session-limit-action stop \
    --short-cooldown-seconds 60 \
    --session-cooldown-seconds 60 \
    --weekly-cooldown-seconds 604800 \
    --rate-limit-threshold 3 \
    --increase-after-clean-chunks 2 \
    --proactive-session-break-minutes 0 \
    2>&1 | tee -a 'logs/${SECOND_SESSION}.tmux.log'"

tmux new-session -d -s "$FINALIZE_SESSION" \
  "cd '$ROOT' && \
   DOMAIN_KEY='$DOMAIN_KEY' \
   DOMAIN_LABEL='$DOMAIN_LABEL' \
   STAGE1='$STAGE1' \
   SECOND='$SECOND_FILE' \
   BOUNDARY_FILE='$BOUNDARY_FILE' \
   BOUNDARY_OUT='$MERGED_DIR' \
   BOUNDARY_PREFIX='${DOMAIN_KEY}_minimax_qwen_boundary_0p2_0p8' \
   FULL_OUT='$FINAL_DIR' \
   FULL_PREFIX='${DOMAIN_KEY}_dual_vote_final_qwen' \
   LLM_TMUX_SESSION='$SECOND_SESSION' \
   LOG='logs/${FINALIZE_SESSION}.log' \
   POLL_SECONDS=60 \
   scripts/run_domain_stage2_dual_vote_finalize.sh"

tmux ls
