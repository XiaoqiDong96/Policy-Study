#!/usr/bin/env bash
set -euo pipefail

ROOT="${ROOT:-$HOME/nev_policy_project}"
cd "$ROOT"

if ! tmux has-session -t future_industries_workflow 2>/dev/null; then
  tmux new-session -d -s future_industries_workflow \
    "cd '$ROOT' && \
     DOMAIN_KEY=future_industries \
     DOMAIN_LABEL='六大未来产业' \
     PIPELINE_SCRIPT=scripts/future_industries_policy_pipeline.py \
     CANDIDATES=outputs/policy_packages_future_lowalt/future_industries/candidates.jsonl \
     OUTPUT_ROOT=outputs/future_industries_policy_panel \
     bash outputs/cloud_runbooks/run_domain_full_workflow_orchestrator.sh"
fi

if ! tmux has-session -t low_altitude_economy_workflow 2>/dev/null; then
  tmux new-session -d -s low_altitude_economy_workflow \
    "cd '$ROOT' && \
     DOMAIN_KEY=low_altitude_economy \
     DOMAIN_LABEL='低空经济' \
     PIPELINE_SCRIPT=scripts/low_altitude_policy_pipeline.py \
     CANDIDATES=outputs/policy_packages_future_lowalt/low_altitude_economy/candidates.jsonl \
     OUTPUT_ROOT=outputs/low_altitude_policy_panel \
     bash outputs/cloud_runbooks/run_domain_full_workflow_orchestrator.sh"
fi

tmux ls
