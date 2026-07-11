#!/usr/bin/env bash
set -euo pipefail

ROOT="${ROOT:-$HOME/nev_policy_project}"
DOMAIN_KEY="${DOMAIN_KEY:?DOMAIN_KEY is required}"
DOMAIN_LABEL="${DOMAIN_LABEL:-$DOMAIN_KEY}"
TOTAL="${TOTAL:-0}"
POLL_SECONDS="${POLL_SECONDS:-60}"

cd "$ROOT"

SECOND="${SECOND:?SECOND is required}"
STAGE1="${STAGE1:?STAGE1 is required}"
BOUNDARY_OUT="${BOUNDARY_OUT:?BOUNDARY_OUT is required}"
BOUNDARY_PREFIX="${BOUNDARY_PREFIX:-${DOMAIN_KEY}_boundary_0p2_0p8}"
FULL_OUT="${FULL_OUT:?FULL_OUT is required}"
FULL_PREFIX="${FULL_PREFIX:-${DOMAIN_KEY}_dual_vote_final}"
LLM_TMUX_SESSION="${LLM_TMUX_SESSION:?LLM_TMUX_SESSION is required}"
LOG="${LOG:-logs/${DOMAIN_KEY}_stage2_dual_finalize.log}"

mkdir -p logs "$BOUNDARY_OUT" "$FULL_OUT"

if [[ "$TOTAL" == "0" ]]; then
  BOUNDARY_FILE="${BOUNDARY_FILE:-outputs/${DOMAIN_KEY}_policy_panel/stage2_dual_vote_boundary/boundary_0p2_0p8_candidates.jsonl}"
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

echo "[finalize] waiting for $DOMAIN_KEY second vote: $SECOND target=$TOTAL" | tee -a "$LOG"
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

echo "[finalize] merging $DOMAIN_KEY boundary votes" | tee -a "$LOG"
python3 scripts/merge_domain_dual_vote_boundary.py \
  --domain-key "$DOMAIN_KEY" \
  --domain-label "$DOMAIN_LABEL" \
  --stage1 "$STAGE1" \
  --second "$SECOND" \
  --output-dir "$BOUNDARY_OUT" \
  --prefix "$BOUNDARY_PREFIX" | tee -a "$LOG"

echo "[finalize] building full $DOMAIN_KEY final classification" | tee -a "$LOG"
python3 scripts/build_domain_dual_vote_final_full.py \
  --domain-key "$DOMAIN_KEY" \
  --domain-label "$DOMAIN_LABEL" \
  --stage1 "$STAGE1" \
  --boundary-merged "$BOUNDARY_OUT/${BOUNDARY_PREFIX}.jsonl" \
  --output-dir "$FULL_OUT" \
  --prefix "$FULL_PREFIX" \
  --export-split-txt | tee -a "$LOG"

echo "[finalize] complete" | tee -a "$LOG"
