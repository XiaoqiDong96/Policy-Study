#!/usr/bin/env bash
set -euo pipefail

ROOT="${ROOT:-$HOME/nev_policy_project}"
TOTAL="${TOTAL:-4288}"
POLL_SECONDS="${POLL_SECONDS:-60}"
LLM_TMUX_SESSION="${LLM_TMUX_SESSION:-nev_high_conf_tool_refine}"

CLASSIFIED="${CLASSIFIED:-outputs/nev_policy_panel/high_conf_confirmed/high_conf_0p9_yes.jsonl}"
TOOL_REFINED="${TOOL_REFINED:-outputs/nev_policy_panel/high_conf_confirmed/tool_refinement/high_conf_0p9_tool_refined.jsonl}"
MERGED_OUTPUT="${MERGED_OUTPUT:-outputs/nev_policy_panel/high_conf_confirmed/refined_classified/high_conf_0p9_refined_classified.jsonl}"
PANEL_OUTPUT_DIR="${PANEL_OUTPUT_DIR:-outputs/nev_policy_panel/high_conf_confirmed/panels}"
LOG="${LOG:-logs/nev_high_conf_tool_finalize.log}"

cd "$ROOT"
mkdir -p logs "$(dirname "$TOOL_REFINED")" "$(dirname "$MERGED_OUTPUT")" "$PANEL_OUTPUT_DIR"

count_lines() {
  if [[ -s "$TOOL_REFINED" ]]; then
    wc -l < "$TOOL_REFINED" | tr -d ' '
  else
    printf '0'
  fi
}

echo "[finalize] waiting for high-confidence tool refinement: target=$TOTAL" | tee -a "$LOG"
while true; do
  done_count="$(count_lines)"
  now="$(date -u '+%Y-%m-%d %H:%M:%S UTC')"
  echo "[finalize] $now tool_refined_done=$done_count/$TOTAL" | tee -a "$LOG"
  if (( done_count >= TOTAL )); then
    break
  fi
  if ! tmux has-session -t "$LLM_TMUX_SESSION" 2>/dev/null; then
    echo "[finalize] ERROR: tool-refinement tmux session $LLM_TMUX_SESSION ended before target count." | tee -a "$LOG"
    exit 2
  fi
  sleep "$POLL_SECONDS"
done

echo "[finalize] merging refined tool fields and building panels" | tee -a "$LOG"
python3 scripts/merge_tool_refinement_and_build_panel.py \
  --classified "$CLASSIFIED" \
  --tool-refined "$TOOL_REFINED" \
  --merged-output "$MERGED_OUTPUT" \
  --panel-output-dir "$PANEL_OUTPUT_DIR" \
  --pipeline-script scripts/nev_policy_pipeline.py \
  --min-confidence 0.55 | tee -a "$LOG"

echo "[finalize] complete" | tee -a "$LOG"
