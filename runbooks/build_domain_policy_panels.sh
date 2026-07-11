#!/usr/bin/env bash
set -euo pipefail

ROOT="${ROOT:-$HOME/nev_policy_project}"
DOMAIN_KEY="${DOMAIN_KEY:?DOMAIN_KEY is required}"
PIPELINE_SCRIPT="${PIPELINE_SCRIPT:?PIPELINE_SCRIPT is required}"
OUTPUT_ROOT="${OUTPUT_ROOT:-outputs/${DOMAIN_KEY}_policy_panel}"
CLASSIFIED="${CLASSIFIED:-${OUTPUT_ROOT}/stage2_dual_vote_boundary/qwen_full/final/${DOMAIN_KEY}_dual_vote_final_qwen_yes.jsonl}"
TOOL_REFINED="${TOOL_REFINED:-${OUTPUT_ROOT}/tool_refinement/${DOMAIN_KEY}_tool_refined.jsonl}"
MERGED_OUTPUT="${MERGED_OUTPUT:-${OUTPUT_ROOT}/tool_refinement/${DOMAIN_KEY}_final_yes_with_tools.jsonl}"
PANEL_OUTPUT_DIR="${PANEL_OUTPUT_DIR:-${OUTPUT_ROOT}/final_panels}"

cd "$ROOT"
. .venv/bin/activate

python scripts/merge_tool_refinement_and_build_panel.py \
  --classified "$CLASSIFIED" \
  --tool-refined "$TOOL_REFINED" \
  --merged-output "$MERGED_OUTPUT" \
  --panel-output-dir "$PANEL_OUTPUT_DIR" \
  --pipeline-script "$PIPELINE_SCRIPT" \
  --min-confidence 0.55 \
  --documents-csv "${DOMAIN_KEY}_policy_documents.csv" \
  --expanded-csv "${DOMAIN_KEY}_policy_expanded_city_month.csv" \
  --panel-csv "${DOMAIN_KEY}_policy_city_month_panel.csv" \
  --central-panel-csv "${DOMAIN_KEY}_policy_central_month_panel.csv" \
  --province-panel-csv "${DOMAIN_KEY}_policy_province_month_panel.csv" \
  --prefecture-panel-csv "${DOMAIN_KEY}_policy_prefecture_month_panel.csv" \
  --summary-json "${DOMAIN_KEY}_policy_summary.json"
