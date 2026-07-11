#!/usr/bin/env bash
set -euo pipefail

ROOT="${ROOT:-$HOME/nev_policy_project}"
TOTAL="${TOTAL:-0}"
POLL_SECONDS="${POLL_SECONDS:-60}"

cd "$ROOT"

SECOND="${SECOND:-outputs/ai_policy_panel/stage2_dual_vote_boundary/qwen_full/qwen_boundary_full.jsonl}"
STAGE1="${STAGE1:-outputs/ai_policy_panel/stage1_minimax_adaptive/ai_stage1_minimax.jsonl}"
BOUNDARY_OUT="${BOUNDARY_OUT:-outputs/ai_policy_panel/stage2_dual_vote_boundary/merged_full_qwen}"
BOUNDARY_PREFIX="${BOUNDARY_PREFIX:-minimax_qwen_boundary_0p2_0p8}"
FULL_OUT="${FULL_OUT:-outputs/ai_policy_panel/stage2_dual_vote_boundary/final_full_qwen}"
FULL_PREFIX="${FULL_PREFIX:-ai_dual_vote_final_qwen}"
LLM_TMUX_SESSION="${LLM_TMUX_SESSION:-ai_stage2_qwen_full}"
LOG="${LOG:-logs/ai_stage2_dual_finalize_qwen.log}"

mkdir -p logs "$BOUNDARY_OUT" "$FULL_OUT"

if [[ "$TOTAL" == "0" ]]; then
  BOUNDARY_FILE="${BOUNDARY_FILE:-outputs/ai_policy_panel/stage2_dual_vote_boundary/boundary_0p2_0p8_candidates.jsonl}"
  if [[ -s "$BOUNDARY_FILE" ]]; then
    TOTAL="$(wc -l < "$BOUNDARY_FILE" | tr -d ' ')"
  else
    echo "[finalize] ERROR: TOTAL was not set and boundary file not found: $BOUNDARY_FILE" | tee -a "$LOG"
    exit 2
  fi
fi

count_lines() {
  if [[ -s "$SECOND" ]]; then
    wc -l < "$SECOND" | tr -d ' '
  else
    printf '0'
  fi
}

echo "[finalize] waiting for AI second vote: $SECOND target=$TOTAL" | tee -a "$LOG"
while true; do
  done_count="$(count_lines)"
  now="$(date -u '+%Y-%m-%d %H:%M:%S UTC')"
  echo "[finalize] $now second_vote_done=$done_count/$TOTAL" | tee -a "$LOG"
  if (( done_count >= TOTAL )); then
    break
  fi
  if ! tmux has-session -t "$LLM_TMUX_SESSION" 2>/dev/null; then
    echo "[finalize] ERROR: LLM tmux session $LLM_TMUX_SESSION ended before target count." | tee -a "$LOG"
    exit 3
  fi
  sleep "$POLL_SECONDS"
done

echo "[finalize] merging AI boundary votes" | tee -a "$LOG"
python3 scripts/merge_ai_dual_vote_boundary.py \
  --stage1 "$STAGE1" \
  --second "$SECOND" \
  --output-dir "$BOUNDARY_OUT" \
  --prefix "$BOUNDARY_PREFIX" | tee -a "$LOG"

echo "[finalize] building full AI final classification" | tee -a "$LOG"
python3 scripts/build_ai_dual_vote_final_full.py \
  --stage1 "$STAGE1" \
  --boundary-merged "$BOUNDARY_OUT/${BOUNDARY_PREFIX}.jsonl" \
  --output-dir "$FULL_OUT" \
  --prefix "$FULL_PREFIX" \
  --export-split-txt | tee -a "$LOG"

echo "[finalize] complete" | tee -a "$LOG"
