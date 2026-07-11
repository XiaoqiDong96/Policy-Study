#!/usr/bin/env bash
set -euo pipefail

ROOT="${ROOT:-$HOME/nev_policy_project}"
DOMAIN_KEY="${DOMAIN_KEY:-culture_industry}"
DOMAIN_LABEL="${DOMAIN_LABEL:-文化产业}"
PIPELINE_SCRIPT="${PIPELINE_SCRIPT:-scripts/culture_industry_policy_pipeline.py}"
CANDIDATES="${CANDIDATES:-outputs/policy_packages_culture/culture_industry/candidates.jsonl}"
OUTPUT_ROOT="${OUTPUT_ROOT:-outputs/culture_industry_policy_panel}"
LOG="${LOG:-logs/culture_industry_full_workflow_orchestrator.log}"

STAGE1="${OUTPUT_ROOT}/stage1_minimax_adaptive/${DOMAIN_KEY}_stage1_minimax.jsonl"
FINAL_FULL="${OUTPUT_ROOT}/stage2_dual_vote_boundary/qwen_full/final/${DOMAIN_KEY}_dual_vote_final_qwen.jsonl"
FINAL_YES="${OUTPUT_ROOT}/stage2_dual_vote_boundary/qwen_full/final/${DOMAIN_KEY}_dual_vote_final_qwen_yes.jsonl"
TOOL_OUT="${OUTPUT_ROOT}/tool_refinement/${DOMAIN_KEY}_tool_refined.jsonl"
MERGED_WITH_TOOLS="${OUTPUT_ROOT}/tool_refinement/${DOMAIN_KEY}_final_yes_with_tools.jsonl"
SUMMARY="${OUTPUT_ROOT}/final_province_category_panels/culture_industry_policy_province_category_summary.json"
COMPLETE_FLAG="${OUTPUT_ROOT}/${DOMAIN_KEY}_full_workflow_complete.flag"

cd "$ROOT"
mkdir -p logs

count_lines() {
  local path="$1"
  if [[ -s "$path" ]]; then
    wc -l < "$path" | tr -d ' '
  else
    printf '0'
  fi
}

log_msg() {
  echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] $*" | tee -a "$LOG"
}

TOTAL="$(count_lines "$CANDIDATES")"
if [[ "$TOTAL" == "0" ]]; then
  log_msg "ERROR: missing or empty candidate file: $CANDIDATES"
  exit 2
fi

log_msg "culture_industry workflow orchestrator started; total=$TOTAL"

while [[ "$(count_lines "$STAGE1")" -lt "$TOTAL" ]]; do
  done_count="$(count_lines "$STAGE1")"
  log_msg "Stage 1 waiting: $done_count/$TOTAL"
  if ! tmux has-session -t "${DOMAIN_KEY}_stage1_minimax" 2>/dev/null; then
    log_msg "Stage 1 tmux missing; launching resumable Stage 1."
    DOMAIN_KEY="$DOMAIN_KEY" PIPELINE_SCRIPT="$PIPELINE_SCRIPT" CANDIDATES="$CANDIDATES" OUTPUT_ROOT="$OUTPUT_ROOT" \
      bash outputs/cloud_runbooks/start_domain_stage1_minimax.sh >> "$LOG" 2>&1 || true
  fi
  sleep 120
done
log_msg "Stage 1 complete: $(count_lines "$STAGE1")/$TOTAL"

while [[ "$(count_lines "$FINAL_FULL")" -lt "$TOTAL" ]]; do
  final_done="$(count_lines "$FINAL_FULL")"
  log_msg "Stage 2/final waiting: $final_done/$TOTAL"
  if ! tmux has-session -t "${DOMAIN_KEY}_stage2_qwen" 2>/dev/null && ! tmux has-session -t "${DOMAIN_KEY}_stage2_finalize" 2>/dev/null; then
    log_msg "Stage 2 sessions missing or not started; launching Qwen boundary vote."
    DOMAIN_KEY="$DOMAIN_KEY" DOMAIN_LABEL="$DOMAIN_LABEL" PIPELINE_SCRIPT="$PIPELINE_SCRIPT" OUTPUT_ROOT="$OUTPUT_ROOT" \
      bash outputs/cloud_runbooks/start_domain_stage2_qwen_boundary.sh >> "$LOG" 2>&1 || true
  fi
  sleep 120
done
log_msg "Stage 2 final complete: $(count_lines "$FINAL_FULL")/$TOTAL"

YES_TOTAL="$(count_lines "$FINAL_YES")"
log_msg "Final yes policies for tool refinement: $YES_TOTAL"
if [[ "$YES_TOTAL" == "0" ]]; then
  log_msg "No final yes policies; writing complete flag without panels."
  date -u '+%Y-%m-%d %H:%M:%S UTC' > "$COMPLETE_FLAG"
  exit 0
fi

while [[ "$(count_lines "$TOOL_OUT")" -lt "$YES_TOTAL" ]]; do
  tool_done="$(count_lines "$TOOL_OUT")"
  log_msg "Tool refinement waiting: $tool_done/$YES_TOTAL"
  if ! tmux has-session -t "${DOMAIN_KEY}_tool_refine" 2>/dev/null; then
    log_msg "Tool refinement tmux missing; launching resumable tool refinement."
    DOMAIN_KEY="$DOMAIN_KEY" DOMAIN_LABEL="$DOMAIN_LABEL" OUTPUT_ROOT="$OUTPUT_ROOT" \
      bash outputs/cloud_runbooks/start_domain_tool_refinement.sh >> "$LOG" 2>&1 || true
  fi
  sleep 120
done
log_msg "Tool refinement complete: $(count_lines "$TOOL_OUT")/$YES_TOTAL"

log_msg "Building culture province-category panels."
DOMAIN_KEY="$DOMAIN_KEY" OUTPUT_ROOT="$OUTPUT_ROOT" \
  bash outputs/cloud_runbooks/build_culture_policy_province_panels.sh >> "$LOG" 2>&1

if [[ -s "$MERGED_WITH_TOOLS" && -s "$SUMMARY" ]]; then
  log_msg "culture_industry workflow complete."
  date -u '+%Y-%m-%d %H:%M:%S UTC' > "$COMPLETE_FLAG"
else
  log_msg "ERROR: culture province panel build did not create expected outputs."
  exit 3
fi
