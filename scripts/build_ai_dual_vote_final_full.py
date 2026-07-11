#!/usr/bin/env python3
"""Build full AI industrial-policy classification after boundary voting."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable


def iter_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            n += 1
    return n


def policy_vote(row: Dict[str, Any]) -> bool:
    cls = row.get("classification") or {}
    domain_related = cls.get("is_ai_related", cls.get("is_nev_related", False))
    return bool(domain_related and cls.get("is_industrial_policy"))


def load_by_id(path: Path) -> Dict[str, Dict[str, Any]]:
    return {str(row.get("id")): row for row in iter_jsonl(path)}


def safe_name(value: str, max_len: int = 90) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|\\s]+", "_", str(value)).strip("_")
    return cleaned[:max_len] or "untitled"


def review_text(row: Dict[str, Any]) -> str:
    merged = row.get("dual_vote", row)
    cls1 = merged.get("stage1_classification") or {}
    cls2 = merged.get("second_classification") or {}
    lines = [
        f"ID: {row.get('id')}",
        f"Title: {row.get('title', '')}",
        f"Province: {row.get('province', '')}",
        f"Date: {row.get('pub_date', '')}",
        "",
        f"Stage1 model: {merged.get('stage1_model', '')}",
        f"Stage1 vote: {merged.get('stage1_vote')}",
        f"Stage1 confidence_is_industrial_policy: {merged.get('stage1_confidence_is_industrial_policy')}",
        f"Stage1 reason: {cls1.get('decision_reason', cls1.get('reason', ''))}",
        f"Stage1 evidence: {json.dumps(cls1.get('direct_target_evidence', ''), ensure_ascii=False)}",
        "",
        f"Second model: {merged.get('second_model', '')}",
        f"Second vote: {merged.get('second_vote')}",
        f"Second confidence_is_industrial_policy: {merged.get('second_confidence_is_industrial_policy')}",
        f"Second reason: {cls2.get('decision_reason', cls2.get('reason', ''))}",
        f"Second evidence: {json.dumps(cls2.get('direct_target_evidence', ''), ensure_ascii=False)}",
    ]
    body = row.get("llm_body") or ""
    if body:
        lines.extend(["", "LLM body/evidence pack:", str(body)[:8000]])
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage1", required=True)
    parser.add_argument("--boundary-merged", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--prefix", default="ai_dual_vote_final")
    parser.add_argument("--export-split-txt", action="store_true")
    args = parser.parse_args()

    out_dir = Path(args.output_dir)
    boundary = load_by_id(Path(args.boundary_merged))
    final_rows = []
    counts: Counter[str] = Counter()

    for stage1 in iter_jsonl(Path(args.stage1)):
        sid = str(stage1.get("id"))
        if sid in boundary:
            merged = boundary[sid]
            label = merged.get("dual_vote_final", "split")
            row = dict(stage1)
            row.update(
                {
                    "domain": "artificial_intelligence",
                    "final_vote_source": "dual_vote_boundary",
                    "dual_vote_final": label,
                    "final_is_ai_industrial_policy": True if label == "yes" else False if label == "no" else None,
                    "requires_review": label == "split",
                    "dual_vote": merged,
                }
            )
        else:
            vote = policy_vote(stage1)
            label = "yes" if vote else "no"
            row = dict(stage1)
            row.update(
                {
                    "domain": "artificial_intelligence",
                    "final_vote_source": "stage1_high_confidence",
                    "dual_vote_final": label,
                    "final_is_ai_industrial_policy": vote,
                    "requires_review": False,
                }
            )
        final_rows.append(row)
        counts[row["dual_vote_final"]] += 1
        counts[row["final_vote_source"]] += 1

    write_jsonl(out_dir / f"{args.prefix}.jsonl", final_rows)
    write_jsonl(out_dir / f"{args.prefix}_yes.jsonl", (r for r in final_rows if r["dual_vote_final"] == "yes"))
    write_jsonl(out_dir / f"{args.prefix}_no.jsonl", (r for r in final_rows if r["dual_vote_final"] == "no"))
    split_rows = [r for r in final_rows if r["dual_vote_final"] == "split"]
    write_jsonl(out_dir / f"{args.prefix}_split.jsonl", split_rows)

    if args.export_split_txt:
        review_dir = out_dir / f"{args.prefix}_split_review_txt"
        review_dir.mkdir(parents=True, exist_ok=True)
        for row in split_rows:
            name = f"{row.get('id')}_{safe_name(row.get('title', ''))}.txt"
            (review_dir / name).write_text(review_text(row), encoding="utf-8")
        counts["split_review_txt_files"] = len(split_rows)

    counts["rows"] = len(final_rows)
    counts["boundary_rows"] = len(boundary)
    (out_dir / f"{args.prefix}_summary.json").write_text(json.dumps(dict(counts), ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(dict(counts), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
