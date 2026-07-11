#!/usr/bin/env bash
set -euo pipefail

ROOT="${ROOT:-$HOME/nev_policy_project}"
DOMAIN_KEY="${DOMAIN_KEY:-culture_industry}"
OUTPUT_ROOT="${OUTPUT_ROOT:-outputs/culture_industry_policy_panel}"
CLASSIFIED="${CLASSIFIED:-${OUTPUT_ROOT}/stage2_dual_vote_boundary/qwen_full/final/${DOMAIN_KEY}_dual_vote_final_qwen_yes.jsonl}"
TOOL_REFINED="${TOOL_REFINED:-${OUTPUT_ROOT}/tool_refinement/${DOMAIN_KEY}_tool_refined.jsonl}"
MERGED_OUTPUT="${MERGED_OUTPUT:-${OUTPUT_ROOT}/tool_refinement/${DOMAIN_KEY}_final_yes_with_tools.jsonl}"
PANEL_OUTPUT_DIR="${PANEL_OUTPUT_DIR:-${OUTPUT_ROOT}/final_province_category_panels}"

cd "$ROOT"
. .venv/bin/activate

python scripts/merge_tool_refinement_and_build_panel.py \
  --classified "$CLASSIFIED" \
  --tool-refined "$TOOL_REFINED" \
  --merged-output "$MERGED_OUTPUT" \
  --panel-output-dir "$PANEL_OUTPUT_DIR" \
  --pipeline-script scripts/culture_industry_policy_pipeline.py \
  --skip-panel

python scripts/build_culture_province_category_panels.py \
  --input "$MERGED_OUTPUT" \
  --output-dir "$PANEL_OUTPUT_DIR" \
  --documents-csv culture_industry_policy_documents_by_category.csv \
  --central-panel-csv culture_industry_policy_central_category_month_panel.csv \
  --province-panel-csv culture_industry_policy_province_category_month_panel.csv \
  --province-with-central-csv culture_industry_policy_province_category_month_panel_with_central.csv \
  --summary-json culture_industry_policy_province_category_summary.json
