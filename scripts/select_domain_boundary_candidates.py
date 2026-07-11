#!/usr/bin/env python3
"""Select boundary industrial-policy cases for a second-model vote."""

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


def domain_related(cls: Dict[str, Any], domain_key: str) -> bool:
    domain_field = f"is_{domain_key}_related"
    if domain_field in cls:
        return bool(cls.get(domain_field))
    if "is_nev_related" in cls:
        return bool(cls.get("is_nev_related"))
    return any(key.startswith("is_") and key.endswith("_related") and bool(value) for key, value in cls.items())


def policy_vote(row: Dict[str, Any], domain_key: str) -> bool:
    cls = row.get("classification") or {}
    return bool(domain_related(cls, domain_key) and cls.get("is_industrial_policy"))


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
    parser.add_argument("--domain-key", required=True)
    parser.add_argument("--input-classified", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--summary", required=True)
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
            counts["boundary_stage1_yes" if policy_vote(row, args.domain_key) else "boundary_stage1_no"] += 1

    out_path = Path(args.output)
    write_jsonl(out_path, selected)
    summary = dict(counts)
    summary.update(
        {
            "domain_key": args.domain_key,
            "low": args.low,
            "high": args.high,
            "output": str(out_path),
            "share": round(counts["boundary_rows"] / counts["input_rows"], 6) if counts["input_rows"] else 0,
        }
    )
    summary_path = Path(args.summary)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
