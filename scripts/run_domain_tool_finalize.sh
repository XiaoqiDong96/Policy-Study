#!/usr/bin/env bash
set -euo pipefail

ROOT="${ROOT:-$HOME/nev_policy_project}"
DOMAIN_KEY="${DOMAIN_KEY:?DOMAIN_KEY is required}"
TOTAL="${TOTAL:-0}"
POLL_SECONDS="${POLL_SECONDS:-60}"
LLM_TMUX_SESSION="${LLM_TMUX_SESSION:?LLM_TMUX_SESSION is required}"

CLASSIFIED="${CLASSIFIED:?CLASSIFIED is required}"
TOOL_REFINED="${TOOL_REFINED:?TOOL_REFINED is required}"
MERGED_OUTPUT="${MERGED_OUTPUT:?MERGED_OUTPUT is required}"
PANEL_OUTPUT_DIR="${PANEL_OUTPUT_DIR:?PANEL_OUTPUT_DIR is required}"
PIPELINE_SCRIPT="${PIPELINE_SCRIPT:?PIPELINE_SCRIPT is required}"
LOG="${LOG:-logs/${DOMAIN_KEY}_tool_finalize.log}"

cd "$ROOT"
mkdir -p logs "$(dirname "$TOOL_REFINED")" "$(dirname "$MERGED_OUTPUT")" "$PANEL_OUTPUT_DIR"

if [[ "$TOTAL" == "0" ]]; then
  if [[ -s "$CLASSIFIED" ]]; then
    TOTAL="$(wc -l < "$CLASSIFIED" | tr -d ' ')"
  else
    echo "[finalize] ERROR: TOTAL was not set and classified file not found: $CLASSIFIED" | tee -a "$LOG"
    exit 2
  fi
fi

count_lines() {
  if [[ -s "$TOOL_REFINED" ]]; then
    wc -l < "$TOOL_REFINED" | tr -d ' '
  else
    printf '0'
  fi
}

echo "[finalize] waiting for $DOMAIN_KEY tool refinement: target=$TOTAL" | tee -a "$LOG"
while true; do
  done_count="$(count_lines)"
  now="$(date -u '+%Y-%m-%d %H:%M:%S UTC')"
  echo "[finalize] $now tool_refined_done=$done_count/$TOTAL" | tee -a "$LOG"
  if (( done_count >= TOTAL )); then
    break
  fi
  if ! tmux has-session -t "$LLM_TMUX_SESSION" 2>/dev/null; then
    echo "[finalize] ERROR: tool-refinement tmux session $LLM_TMUX_SESSION ended before target count." | tee -a "$LOG"
    exit 3
  fi
  sleep "$POLL_SECONDS"
done

echo "[finalize] merging refined tool fields and building $DOMAIN_KEY panels" | tee -a "$LOG"
python3 scripts/merge_tool_refinement_and_build_panel.py \
  --classified "$CLASSIFIED" \
  --tool-refined "$TOOL_REFINED" \
  --merged-output "$MERGED_OUTPUT" \
  --panel-output-dir "$PANEL_OUTPUT_DIR" \
  --pipeline-script "$PIPELINE_SCRIPT" \
  --documents-csv "${DOMAIN_KEY}_policy_documents.csv" \
  --expanded-csv "${DOMAIN_KEY}_policy_expanded_city_month.csv" \
  --panel-csv "${DOMAIN_KEY}_policy_city_month_panel.csv" \
  --central-panel-csv "${DOMAIN_KEY}_policy_central_month_panel.csv" \
  --province-panel-csv "${DOMAIN_KEY}_policy_province_month_panel.csv" \
  --prefecture-panel-csv "${DOMAIN_KEY}_policy_prefecture_month_panel.csv" \
  --summary-json "${DOMAIN_KEY}_policy_summary.json" \
  --min-confidence 0.55 | tee -a "$LOG"

echo "[finalize] complete" | tee -a "$LOG"
