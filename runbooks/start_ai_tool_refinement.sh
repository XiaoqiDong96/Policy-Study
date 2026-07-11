#!/usr/bin/env bash
set -euo pipefail

cd ~/nev_policy_project
. .venv/bin/activate

python scripts/ai_policy_tool_refiner.py \
  --input-classified outputs/ai_policy_panel/stage2_dual_vote_boundary/final_full_qwen/ai_dual_vote_final_qwen_yes.jsonl \
  --output outputs/ai_policy_panel/tool_refinement/ai_tool_refined.jsonl \
  --model minimax-m2.5:cloud \
  --parallel-docs 8 \
  --max-body-chars 8000 \
  --num-ctx 16384 \
  --llm-timeout 600 \
  --llm-retries 4 \
  --retry-base-sleep 8 \
  --progress-every 100
