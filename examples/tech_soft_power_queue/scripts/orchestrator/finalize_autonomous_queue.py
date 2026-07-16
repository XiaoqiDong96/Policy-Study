#!/usr/bin/env python3
"""Write the final, machine-verifiable acceptance record for the cloud queue.

This command is the last command of CR16.  It re-evaluates every manifest gate
instead of trusting task exit codes, records hashes for all gated files, and
fails if any upstream task is neither complete nor conditionally skipped with
evidence.  Missing data remain a documented limitation; they are never turned
into synthetic observations by this finalizer.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cloud_queue_runner import QueueRunner, SUCCESS_STATES


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def relative_or_text(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def parse_args() -> argparse.Namespace:
    default_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=default_root)
    parser.add_argument("--manifest", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.project_root.resolve()
    manifest = args.manifest or root / "scripts/orchestrator/task_manifest.json"
    # This process is launched by CR16 while the parent queue legitimately
    # records CR16 as RUNNING.  Load that state for auditing without applying
    # the queue-startup stale-RUNNING recovery rule.
    runner = QueueRunner(root, manifest, recover_stale_running=False)
    self_outputs = {
        (root / "10_qc/orchestrator/final_acceptance.json").resolve(),
        (root / "10_qc/orchestrator/final_output_inventory.csv").resolve(),
        (root / "10_qc/orchestrator/FINAL_ACCEPTANCE.md").resolve(),
    }

    task_results: list[dict[str, Any]] = []
    inventory: dict[str, dict[str, Any]] = {}
    failures: list[str] = []
    for task in sorted(
        runner.task_map.values(), key=lambda item: int(item.get("order", 999))
    ):
        task_id = task["task_id"]
        state = runner.state["tasks"][task_id]
        state_ok = state.get("status") in SUCCESS_STATES or (
            task_id == "CR16" and state.get("status") == "RUNNING"
        )
        # The runner checks the finalizer's own outputs after this command
        # returns.  Excluding those two circular gates here lets the finalizer
        # audit every pre-existing deliverable before atomically writing them.
        gates = [
            runner._evaluate_gate(gate)
            for gate in task.get("gates", [])
            if runner._gate_path(gate).resolve() not in self_outputs
        ]
        gates_ok = bool(gates) and all(result.get("passed") for result in gates)
        if not state_ok:
            failures.append(f"{task_id}:state={state.get('status')}")
        if not gates_ok:
            failures.append(f"{task_id}:gate_failure")
        task_results.append(
            {
                "task_id": task_id,
                "task_name": task.get("task_name", ""),
                "state": state.get("status"),
                "state_accepted": state_ok,
                "gates_passed": gates_ok,
                "gates": [
                    {
                        **result,
                        "path": relative_or_text(Path(result["path"]), root),
                    }
                    for result in gates
                ],
            }
        )
        for gate in task.get("gates", []):
            path = runner._gate_path(gate)  # Same manifest expansion as the runner.
            if path.resolve() in self_outputs:
                continue
            if path.is_file():
                key = relative_or_text(path, root)
                inventory[key] = {
                    "path": key,
                    "bytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                    "gate_tasks": sorted(
                        set(inventory.get(key, {}).get("gate_tasks", [])) | {task_id}
                    ),
                }

    inventory_path = root / "10_qc/orchestrator/final_output_inventory.csv"
    inventory_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = inventory_path.with_suffix(".csv.tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        fields = ["path", "bytes", "sha256", "gate_tasks"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in sorted(inventory.values(), key=lambda item: item["path"]):
            writer.writerow({**row, "gate_tasks": "|".join(row["gate_tasks"])})
    temporary.replace(inventory_path)

    status = "PASS" if not failures else "FAIL"
    payload = {
        "status": status,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "manifest": relative_or_text(manifest, root),
        "task_count": len(task_results),
        "tasks_accepted": sum(
            bool(row["state_accepted"] and row["gates_passed"]) for row in task_results
        ),
        "failures": failures,
        "missing_value_policy": "preserved; no implicit zero or imputation",
        "excluded_inputs": [
            "air-quality series",
            "historical_IV_city.dta",
            "unvalidated recruitment extracts other than the approved listed-company archive",
        ],
        "inventory": relative_or_text(inventory_path, root),
        "inventory_rows": len(inventory),
        "task_results": task_results,
    }
    output = root / "10_qc/orchestrator/final_acceptance.json"
    atomic_json(output, payload)
    report_lines = [
        "# 297-city technology soft-power data acceptance",
        "",
        f"- Status: **{status}**",
        f"- Generated (UTC): {payload['generated_at_utc']}",
        f"- Accepted tasks: {payload['tasks_accepted']}/{payload['task_count']}",
        "- Candidate grid: 297 cities, 2012–2026 (4,455 rows)",
        "- Missing-value rule: preserve missingness; no implicit zero or imputation",
        "- Scope: candidate data freeze only; scaling, weighting, and composite scores remain separate research-design steps",
        "",
        "## Task acceptance",
        "",
        "| Task | State | Gates |",
        "|---|---:|---:|",
    ]
    for row in task_results:
        name = str(row["task_name"]).replace("|", "\\|")
        report_lines.append(
            f"| {row['task_id']} {name} | {row['state']} | "
            f"{'PASS' if row['gates_passed'] else 'FAIL'} |"
        )
    report_lines.extend(
        [
            "",
            "## Deliberate exclusions",
            "",
            "- Air-quality series (paused by research decision)",
            "- `historical_IV_city.dta` (excluded as not meaningful for this construct)",
            "- Unvalidated recruitment extracts other than the approved listed-company archive",
            "",
            "## Reproducibility",
            "",
            f"The gated deliverable inventory and SHA-256 hashes are recorded in `{payload['inventory']}`.",
        ]
    )
    if failures:
        report_lines.extend(["", "## Failures", ""] + [f"- {item}" for item in failures])
    atomic_text(
        root / "10_qc/orchestrator/FINAL_ACCEPTANCE.md",
        "\n".join(report_lines) + "\n",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if status == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
