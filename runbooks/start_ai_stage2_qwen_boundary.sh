#!/usr/bin/env bash
set -euo pipefail

cd ~/nev_policy_project
. .venv/bin/activate

python scripts/select_ai_boundary_candidates.py \
  --input-classified outputs/ai_policy_panel/stage1_minimax_adaptive/ai_stage1_minimax.jsonl \
  --output outputs/ai_policy_panel/stage2_dual_vote_boundary/boundary_0p2_0p8_candidates.jsonl \
  --summary outputs/ai_policy_panel/stage2_dual_vote_boundary/boundary_0p2_0p8_summary.json \
  --low 0.2 \
  --high 0.8

if tmux has-session -t ai_stage2_qwen_full 2>/dev/null; then
  tmux kill-session -t ai_stage2_qwen_full
fi
if tmux has-session -t ai_stage2_dual_finalize 2>/dev/null; then
  tmux kill-session -t ai_stage2_dual_finalize
fi

mkdir -p outputs/ai_policy_panel/stage2_dual_vote_boundary/qwen_full logs

tmux new-session -d -s ai_stage2_qwen_full \
  "cd ~/nev_policy_project && . .venv/bin/activate && python scripts/ollama_cloud_adaptive_runner.py \
    --pipeline-script scripts/ai_policy_pipeline.py \
    --candidates outputs/ai_policy_panel/stage2_dual_vote_boundary/boundary_0p2_0p8_candidates.jsonl \
    --output-dir outputs/ai_policy_panel/stage2_dual_vote_boundary/qwen_full \
    --candidates-name qwen_boundary_candidates_norm.jsonl \
    --classified-name qwen_boundary_full.jsonl \
    --model qwen3.5:cloud \
    --initial-parallel-docs 4 \
    --min-parallel-docs 1 \
    --max-parallel-docs 4 \
    --chunk-size 300 \
    --prompt-mode standard \
    --ollama-format auto \
    --max-body-chars 8000 \
    --long-doc-mode evidence_pack \
    --num-ctx 16384 \
    --llm-timeout 900 \
    --llm-retries 4 \
    --retry-base-sleep 8 \
    --progress-every 50 \
    --weekly-limit-action stop \
    --session-limit-action sleep \
    --short-cooldown-seconds 1200 \
    --session-cooldown-seconds 18300 \
    --weekly-cooldown-seconds 604800 \
    --rate-limit-threshold 3 \
    --increase-after-clean-chunks 3 \
    --proactive-session-break-minutes 285 \
    2>&1 | tee -a logs/ai_stage2_qwen_full.tmux.log"

tmux new-session -d -s ai_stage2_dual_finalize \
  "cd ~/nev_policy_project && \
   SECOND=outputs/ai_policy_panel/stage2_dual_vote_boundary/qwen_full/qwen_boundary_full.jsonl \
   BOUNDARY_FILE=outputs/ai_policy_panel/stage2_dual_vote_boundary/boundary_0p2_0p8_candidates.jsonl \
   BOUNDARY_OUT=outputs/ai_policy_panel/stage2_dual_vote_boundary/merged_full_qwen \
   BOUNDARY_PREFIX=minimax_qwen_boundary_0p2_0p8 \
   FULL_OUT=outputs/ai_policy_panel/stage2_dual_vote_boundary/final_full_qwen \
   FULL_PREFIX=ai_dual_vote_final_qwen \
   LLM_TMUX_SESSION=ai_stage2_qwen_full \
   LOG=logs/ai_stage2_dual_finalize_qwen.log \
   POLL_SECONDS=60 \
   scripts/run_ai_stage2_dual_vote_finalize.sh"

tmux ls
