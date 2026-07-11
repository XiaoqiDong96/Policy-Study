#!/usr/bin/env python3
"""
Select AI industrial-policy boundary cases for a second-model vote.

Default boundary is confidence_is_industrial_policy in [0.2, 0.8], matching
the NEV workflow. The output rows preserve the original classified row fields
so they can be fed directly into ai_policy_pipeline.py classify through
--existing-candidates.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


def iter_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def confidence(row: Dict[str, Any], key: str = "confidence_is_industrial_policy") -> Optional[float]:
    try:
        value = (row.get("classification") or {}).get(key)
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def policy_vote(row: Dict[str, Any]) -> bool:
    cls = row.get("classification") or {}
    domain_related = cls.get("is_ai_related", cls.get("is_nev_related", False))
    return bool(domain_related and cls.get("is_industrial_policy"))


def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            n += 1
    return n


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-classified", default="outputs/ai_policy_panel/stage1_minimax_adaptive/ai_stage1_minimax.jsonl")
    parser.add_argument("--output", default="outputs/ai_policy_panel/stage2_dual_vote_boundary/boundary_0p2_0p8_candidates.jsonl")
    parser.add_argument("--summary", default="outputs/ai_policy_panel/stage2_dual_vote_boundary/boundary_0p2_0p8_summary.json")
    parser.add_argument("--low", type=float, default=0.2)
    parser.add_argument("--high", type=float, default=0.8)
    args = parser.parse_args()

    selected = []
    counts: Counter[str] = Counter()
    for row in iter_jsonl(Path(args.input_classified)):
        counts["input_rows"] += 1
        c = confidence(row)
        if c is None:
            counts["missing_confidence"] += 1
            continue
        if args.low <= c <= args.high:
            selected.append(row)
            counts["boundary_rows"] += 1
            counts["boundary_stage1_yes" if policy_vote(row) else "boundary_stage1_no"] += 1

    out_path = Path(args.output)
    write_jsonl(out_path, selected)
    summary = dict(counts)
    summary.update(
        {
            "low": args.low,
            "high": args.high,
            "output": str(out_path),
            "share": round(counts["boundary_rows"] / counts["input_rows"], 6) if counts["input_rows"] else 0,
        }
    )
    Path(args.summary).parent.mkdir(parents=True, exist_ok=True)
    Path(args.summary).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
