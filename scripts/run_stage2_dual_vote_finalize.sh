#!/usr/bin/env bash
set -euo pipefail

ROOT="${ROOT:-$HOME/nev_policy_project}"
TOTAL="${TOTAL:-12703}"
POLL_SECONDS="${POLL_SECONDS:-60}"

cd "$ROOT"

SECOND="${SECOND:-outputs/nev_policy_panel/stage2_dual_vote_boundary/gemini_full/gemini_boundary_full.jsonl}"
STAGE1="${STAGE1:-outputs/nev_policy_panel/stage1_minimax_adaptive/nev_stage1_minimax.jsonl}"
BOUNDARY_OUT="${BOUNDARY_OUT:-outputs/nev_policy_panel/stage2_dual_vote_boundary/merged_full}"
BOUNDARY_PREFIX="${BOUNDARY_PREFIX:-minimax_gemini_boundary_0p2_0p8}"
FULL_OUT="${FULL_OUT:-outputs/nev_policy_panel/stage2_dual_vote_boundary/final_full}"
FULL_PREFIX="${FULL_PREFIX:-nev_dual_vote_final}"
LLM_TMUX_SESSION="${LLM_TMUX_SESSION:-nev_stage2_gemini_full}"
LOG="${LOG:-logs/nev_stage2_dual_finalize.log}"

mkdir -p logs "$BOUNDARY_OUT" "$FULL_OUT"

count_lines() {
  if [[ -s "$SECOND" ]]; then
    wc -l < "$SECOND" | tr -d ' '
  else
    printf '0'
  fi
}

echo "[finalize] waiting for second vote: $SECOND target=$TOTAL" | tee -a "$LOG"
while true; do
  done_count="$(count_lines)"
  now="$(date -u '+%Y-%m-%d %H:%M:%S UTC')"
  echo "[finalize] $now second_vote_done=$done_count/$TOTAL" | tee -a "$LOG"
  if (( done_count >= TOTAL )); then
    break
  fi
  if ! tmux has-session -t "$LLM_TMUX_SESSION" 2>/dev/null; then
    echo "[finalize] ERROR: LLM tmux session $LLM_TMUX_SESSION ended before target count." | tee -a "$LOG"
    exit 2
  fi
  sleep "$POLL_SECONDS"
done

echo "[finalize] merging boundary votes" | tee -a "$LOG"
python3 scripts/merge_dual_vote_boundary.py \
  --stage1 "$STAGE1" \
  --second "$SECOND" \
  --output-dir "$BOUNDARY_OUT" \
  --prefix "$BOUNDARY_PREFIX" | tee -a "$LOG"

echo "[finalize] building full final classification" | tee -a "$LOG"
python3 scripts/build_dual_vote_final_full.py \
  --stage1 "$STAGE1" \
  --boundary-merged "$BOUNDARY_OUT/${BOUNDARY_PREFIX}.jsonl" \
  --output-dir "$FULL_OUT" \
  --prefix "$FULL_PREFIX" \
  --export-split-txt | tee -a "$LOG"

echo "[finalize] complete" | tee -a "$LOG"
