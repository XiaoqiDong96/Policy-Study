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

audit_tool_output() {
  python3 - "$FINAL_YES" "$TOOL_OUT" <<'PY'
import json
import sys
from pathlib import Path

expected_path, output_path = map(Path, sys.argv[1:])
expected_ids = []
with expected_path.open(encoding="utf-8") as handle:
    for line in handle:
        row = json.loads(line)
        expected_ids.append(str(row.get("id", "")).strip())
expected = {value for value in expected_ids if value}

valid = set()
invalid = 0
duplicates = 0
if output_path.is_file():
    with output_path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except (TypeError, json.JSONDecodeError):
                invalid += 1
                continue
            record_id = str(row.get("id", "")).strip()
            if not record_id or record_id not in expected or row.get("tool_error"):
                invalid += 1
                continue
            if record_id in valid:
                duplicates += 1
            valid.add(record_id)

missing = len(expected - valid)
print(f"{len(expected)}|{len(valid)}|{invalid}|{duplicates}|{missing}")
PY
}

clean_tool_output() {
  python3 - "$FINAL_YES" "$TOOL_OUT" <<'PY'
import json
import os
import sys
from pathlib import Path

expected_path, output_path = map(Path, sys.argv[1:])
expected_ids = []
with expected_path.open(encoding="utf-8") as handle:
    for line in handle:
        row = json.loads(line)
        record_id = str(row.get("id", "")).strip()
        if record_id:
            expected_ids.append(record_id)
expected = set(expected_ids)

valid = {}
if output_path.is_file():
    with output_path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except (TypeError, json.JSONDecodeError):
                continue
            record_id = str(row.get("id", "")).strip()
            if record_id in expected and not row.get("tool_error"):
                valid[record_id] = row

temporary = output_path.with_suffix(output_path.suffix + ".clean.tmp")
with temporary.open("w", encoding="utf-8") as handle:
    for record_id in expected_ids:
        if record_id in valid:
            handle.write(json.dumps(valid[record_id], ensure_ascii=False) + "\n")
os.replace(temporary, output_path)
print(len(valid))
PY
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

while true; do
  IFS='|' read -r expected_unique valid_count invalid_count duplicate_count missing_count \
    <<< "$(audit_tool_output)"
  if [[ "$expected_unique" -ne "$YES_TOTAL" ]]; then
    log_msg "ERROR: final-yes ids are not unique: lines=$YES_TOTAL unique=$expected_unique"
    exit 4
  fi
  if tmux has-session -t "${DOMAIN_KEY}_tool_refine" 2>/dev/null; then
    log_msg "Tool refinement waiting: valid=$valid_count/$YES_TOTAL invalid=$invalid_count duplicates=$duplicate_count missing=$missing_count"
    sleep 120
    continue
  fi
  if [[ "$valid_count" -eq "$YES_TOTAL" && "$invalid_count" -eq 0 && "$duplicate_count" -eq 0 && "$missing_count" -eq 0 ]]; then
    break
  fi
  if [[ "$invalid_count" -gt 0 || "$duplicate_count" -gt 0 ]]; then
    cleaned="$(clean_tool_output)"
    log_msg "Removed invalid/error/duplicate tool rows; retained=$cleaned/$YES_TOTAL."
  fi
  log_msg "Tool refinement incomplete; launching resumable retry."
  DOMAIN_KEY="$DOMAIN_KEY" DOMAIN_LABEL="$DOMAIN_LABEL" OUTPUT_ROOT="$OUTPUT_ROOT" \
    bash outputs/cloud_runbooks/start_domain_tool_refinement.sh >> "$LOG" 2>&1 || true
  sleep 120
done
log_msg "Tool refinement complete and error-free: $YES_TOTAL/$YES_TOTAL"

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
