#!/usr/bin/env python3
"""Merge a domain stage-1 file with one additional model for boundary samples."""

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


def confidence(row: Dict[str, Any], key: str = "confidence_is_industrial_policy") -> Optional[float]:
    cls = row.get("classification") or {}
    try:
        value = cls.get(key)
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def load_by_id(path: Path) -> Dict[str, Dict[str, Any]]:
    return {str(row.get("id")): row for row in iter_jsonl(path)}


def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            n += 1
    return n


def merge_rows(stage1: Dict[str, Any], second: Dict[str, Any], domain_key: str, domain_label: str) -> Dict[str, Any]:
    vote1 = policy_vote(stage1, domain_key)
    vote2 = policy_vote(second, domain_key)
    c1 = confidence(stage1)
    c2 = confidence(second)
    avg_conf = round((c1 + c2) / 2, 4) if c1 is not None and c2 is not None else None
    final_label = "yes" if vote1 and vote2 else "no" if (not vote1 and not vote2) else "split"
    return {
        "id": stage1.get("id"),
        "title": stage1.get("title", ""),
        "province": stage1.get("province", ""),
        "pub_date": stage1.get("pub_date", ""),
        "admin": stage1.get("admin", {}),
        "domain": domain_key,
        "domain_label": domain_label,
        "stage1_model": stage1.get("llm_model", ""),
        "stage1_vote": vote1,
        "stage1_confidence_is_industrial_policy": c1,
        "stage1_classification_confidence": confidence(stage1, "classification_confidence"),
        "second_model": second.get("llm_model", ""),
        "second_vote": vote2,
        "second_confidence_is_industrial_policy": c2,
        "second_classification_confidence": confidence(second, "classification_confidence"),
        "dual_vote_agreement": vote1 == vote2,
        "split_vote": vote1 != vote2,
        "dual_vote_final": final_label,
        "dual_vote_avg_confidence_is_industrial_policy": avg_conf,
        "stage1_classification": stage1.get("classification", {}),
        "second_classification": second.get("classification", {}),
        "second_model_error": second.get("llm_error", ""),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--domain-key", required=True)
    parser.add_argument("--domain-label", default="")
    parser.add_argument("--stage1", required=True)
    parser.add_argument("--second", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--prefix", default="domain_dual_vote_boundary")
    args = parser.parse_args()

    stage1 = load_by_id(Path(args.stage1))
    second = load_by_id(Path(args.second))
    out_dir = Path(args.output_dir)

    merged = []
    missing = []
    for sid, second_row in second.items():
        base = stage1.get(sid)
        if base is None:
            missing.append(sid)
            continue
        merged.append(merge_rows(base, second_row, args.domain_key, args.domain_label or args.domain_key))

    counts = Counter(row["dual_vote_final"] for row in merged)
    counts["missing_stage1"] = len(missing)
    counts["rows"] = len(merged)
    summary = dict(counts)
    summary["domain_key"] = args.domain_key

    write_jsonl(out_dir / f"{args.prefix}.jsonl", merged)
    write_jsonl(out_dir / f"{args.prefix}_agreement_yes.jsonl", (r for r in merged if r["dual_vote_final"] == "yes"))
    write_jsonl(out_dir / f"{args.prefix}_agreement_no.jsonl", (r for r in merged if r["dual_vote_final"] == "no"))
    write_jsonl(out_dir / f"{args.prefix}_split.jsonl", (r for r in merged if r["dual_vote_final"] == "split"))
    (out_dir / f"{args.prefix}_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
