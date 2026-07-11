#!/usr/bin/env bash
set -euo pipefail

cd ~/nev_policy_project
. .venv/bin/activate

python scripts/ollama_cloud_adaptive_runner.py \
  --pipeline-script scripts/ai_policy_pipeline.py \
  --candidates outputs/policy_packages/artificial_intelligence/candidates.jsonl \
  --output-dir outputs/ai_policy_panel/stage1_minimax_adaptive \
  --candidates-name ai_stage1_candidates_norm.jsonl \
  --classified-name ai_stage1_minimax.jsonl \
  --model minimax-m2.5:cloud \
  --initial-parallel-docs 8 \
  --min-parallel-docs 2 \
  --max-parallel-docs 8 \
  --chunk-size 3000 \
  --prompt-mode standard \
  --ollama-format auto \
  --max-body-chars 8000 \
  --long-doc-mode evidence_pack \
  --num-ctx 16384 \
  --llm-timeout 600 \
  --llm-retries 4 \
  --retry-base-sleep 5 \
  --progress-every 100 \
  --weekly-limit-action stop \
  --session-limit-action sleep \
  --short-cooldown-seconds 900 \
  --session-cooldown-seconds 18300 \
  --rate-limit-threshold 3 \
  --proactive-session-break-minutes 0
