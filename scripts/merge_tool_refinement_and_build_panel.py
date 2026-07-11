#!/usr/bin/env python3
"""Merge tool refinement results back into classified rows and build panels."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable


TOOL_KEYS = [
    "measure_specificity",
    "policy_tone",
    "timing",
    "policy_side",
    "policy_tools",
    "tool_groups",
    "target_segments",
    "specific_measures",
    "eligibility_conditions",
    "implementation_mechanisms",
    "strength_score",
    "coverage_breadth_score",
]


def iter_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def load_tools(path: Path) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    if not path.exists():
        return out
    for row in iter_jsonl(path):
        out[str(row.get("id"))] = row
    return out


def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            n += 1
    return n


def merged_rows(classified_path: Path, tool_path: Path) -> Iterable[Dict[str, Any]]:
    tools = load_tools(tool_path)
    for row in iter_jsonl(classified_path):
        merged = dict(row)
        cls = dict(merged.get("classification") or {})
        tool_row = tools.get(str(row.get("id")))
        if tool_row:
            tool_cls = tool_row.get("tool_classification") or {}
            for key in TOOL_KEYS:
                if key in tool_cls:
                    cls[key] = tool_cls[key]
            merged["tool_refinement"] = tool_row
            merged["tool_refinement_error"] = tool_row.get("tool_error", "")
        else:
            merged["tool_refinement_error"] = "missing_tool_refinement"
        merged["classification"] = cls
        yield merged


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--classified", required=True)
    parser.add_argument("--tool-refined", required=True)
    parser.add_argument("--merged-output", required=True)
    parser.add_argument("--panel-output-dir", required=True)
    parser.add_argument("--pipeline-script", default="scripts/nev_policy_pipeline.py")
    parser.add_argument("--min-confidence", type=float, default=0.55)
    parser.add_argument("--documents-csv", default="high_conf_nev_policy_documents.csv")
    parser.add_argument("--expanded-csv", default="high_conf_nev_policy_expanded_city_month.csv")
    parser.add_argument("--panel-csv", default="high_conf_nev_policy_city_month_panel.csv")
    parser.add_argument("--central-panel-csv", default="high_conf_nev_policy_central_month_panel.csv")
    parser.add_argument("--province-panel-csv", default="high_conf_nev_policy_province_month_panel.csv")
    parser.add_argument("--prefecture-panel-csv", default="high_conf_nev_policy_prefecture_month_panel.csv")
    parser.add_argument("--summary-json", default="high_conf_nev_policy_summary.json")
    args = parser.parse_args()

    classified = Path(args.classified)
    tool_refined = Path(args.tool_refined)
    merged_output = Path(args.merged_output)
    panel_output_dir = Path(args.panel_output_dir)

    count = write_jsonl(merged_output, merged_rows(classified, tool_refined))
    print(f"Merged rows: {count:,} -> {merged_output}", flush=True)

    cmd = [
        sys.executable,
        args.pipeline_script,
        "panel",
        "--classified",
        str(merged_output),
        "--output-dir",
        str(panel_output_dir),
        "--min-confidence",
        str(args.min_confidence),
        "--documents-csv",
        args.documents_csv,
        "--expanded-csv",
        args.expanded_csv,
        "--panel-csv",
        args.panel_csv,
        "--central-panel-csv",
        args.central_panel_csv,
        "--province-panel-csv",
        args.province_panel_csv,
        "--prefecture-panel-csv",
        args.prefecture_panel_csv,
        "--summary-json",
        args.summary_json,
    ]
    print("Running panel command:", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


if __name__ == "__main__":
    main()
