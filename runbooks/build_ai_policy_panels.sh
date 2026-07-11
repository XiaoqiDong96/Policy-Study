#!/usr/bin/env bash
set -euo pipefail

cd ~/nev_policy_project
. .venv/bin/activate

python scripts/ai_policy_pipeline.py panel \
  --classified outputs/ai_policy_panel/stage2_dual_vote_boundary/final_full_qwen/ai_dual_vote_final_qwen_yes.jsonl \
  --output-dir outputs/ai_policy_panel/panels \
  --min-confidence 0.55 \
  --documents-csv ai_policy_documents.csv \
  --expanded-csv ai_policy_expanded_city_month.csv \
  --panel-csv ai_policy_city_month_panel.csv \
  --central-panel-csv ai_policy_central_month_panel.csv \
  --province-panel-csv ai_policy_province_month_panel.csv \
  --prefecture-panel-csv ai_policy_prefecture_month_panel.csv \
  --summary-json ai_policy_summary.json
