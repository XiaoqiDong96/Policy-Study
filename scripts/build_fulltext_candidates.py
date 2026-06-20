#!/usr/bin/env python3
"""
Materialize full-text NEV candidate JSONL from the original legal corpus.

The high-recall NEV package in outputs/policy_packages/new_energy_vehicle keeps
only a body snippet. This helper streams the original 84GB JSON array once,
matches candidate IDs, cleans HTML with BeautifulSoup via the main pipeline
helpers, and writes a JSONL package with llm_body containing the full cleaned
text. Classification can then decide whether to send the full body or compress
only overlong documents.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

from nev_policy_pipeline import clean_text, format_duration, infer_admin, iter_json_records, parse_month  # noqa: E402


DROP_SNIPPET_FIELDS = {"body_snippet"}


def load_candidate_meta(path: Path) -> Dict[str, Dict[str, Any]]:
    meta: Dict[str, Dict[str, Any]] = {}
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            doc_id = str(row.get("id"))
            if not doc_id or doc_id == "None":
                continue
            for field in DROP_SNIPPET_FIELDS:
                row.pop(field, None)
            meta[doc_id] = row
    return meta


def merge_fulltext_record(candidate: Dict[str, Any], record: Dict[str, Any], full_text: str) -> Dict[str, Any]:
    out = dict(candidate)
    month = out.get("date_month")
    precision = out.get("date_precision")
    if not month:
        month, precision = parse_month(record.get("pub_date"), record.get("use_date"), record.get("IssueDate"))

    for key in [
        "law_db_name",
        "title",
        "detail_url",
        "province",
        "EffectivenessDic",
        "TimelinessDic",
        "IssueDate",
        "IssueDepartment_2",
        "IssueDepartment_3",
        "category_1",
        "category_2",
        "law_type",
        "pub_depart",
        "pub_num",
        "pub_date",
        "use_date",
        "is_time",
    ]:
        if not out.get(key) and record.get(key) is not None:
            out[key] = record.get(key)

    out["date_month"] = month
    out["date_precision"] = precision or "missing"
    out["admin"] = out.get("admin") if isinstance(out.get("admin"), dict) else infer_admin(out)
    out["llm_body"] = full_text
    out["full_text_char_len"] = len(full_text)
    out["text_char_len"] = len(full_text)
    out["fulltext_source"] = "法律法规文件库.json/detail_html_or_detail_flag"
    return out


def progress_line(scanned: int, found: int, target: int, started: float, final: bool = False) -> str:
    elapsed = max(0.1, time.time() - started)
    scan_rate = scanned / elapsed
    found_rate = found / elapsed if found else 0.0
    remaining_found = max(0, target - found)
    eta = remaining_found / found_rate if found_rate else None
    tag = "FULLTEXT FINAL" if final else "FULLTEXT"
    return (
        f"[{tag}] scanned={scanned:,} found={found:,}/{target:,} "
        f"scan_rate={scan_rate:,.0f}/s found_rate={found_rate*60:,.2f}/min "
        f"ETA={format_duration(eta)} elapsed={format_duration(elapsed)}"
    )


def write_sample(path: Path, sample: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as fh:
        for row in sample:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    tmp_path.replace(path)


def build_fulltext_candidates(args: argparse.Namespace) -> Tuple[int, int]:
    source_path = Path(args.input)
    candidates_path = Path(args.candidates)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    candidate_meta = load_candidate_meta(candidates_path)
    target_count = len(candidate_meta)
    target_ids = set(candidate_meta)
    print(f"Loaded candidate IDs: {target_count:,} from {candidates_path}", flush=True)

    rng = random.Random(args.sample_seed)
    sample: List[Dict[str, Any]] = []
    sample_size = max(0, int(args.sample_size or 0))

    started = time.time()
    scanned = found = 0
    tmp_output = output_path.with_suffix(output_path.suffix + ".tmp")
    with tmp_output.open("w", encoding="utf-8") as out_fh:
        for record in iter_json_records(source_path, args.json_prefix):
            scanned += 1
            doc_id = str(record.get("id"))
            if doc_id in target_ids:
                full_text = clean_text(record)
                row = merge_fulltext_record(candidate_meta[doc_id], record, full_text)
                out_fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                found += 1

                if sample_size:
                    if len(sample) < sample_size:
                        sample.append(row)
                    else:
                        pick = rng.randrange(found)
                        if pick < sample_size:
                            sample[pick] = row

                if found % max(1, args.found_progress_every) == 0:
                    print(progress_line(scanned, found, target_count, started), flush=True)
                if found >= target_count:
                    break

            if scanned % args.progress_every == 0:
                print(progress_line(scanned, found, target_count, started), flush=True)

    tmp_output.replace(output_path)
    print(progress_line(scanned, found, target_count, started, final=True), flush=True)
    print(f"Full-text candidates: {output_path}", flush=True)

    missing = sorted(target_ids - {str(row.get("id")) for row in sample}) if found == len(sample) else []
    if found != target_count:
        missing_path = output_path.with_suffix(".missing_ids.txt")
        found_ids = set()
        with output_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    found_ids.add(str(json.loads(line).get("id")))
        missing = sorted(target_ids - found_ids)
        missing_path.write_text("\n".join(missing), encoding="utf-8")
        print(f"WARNING: missing={len(missing):,}; wrote {missing_path}", flush=True)

    if sample_size and args.sample_output:
        sample_path = Path(args.sample_output)
        write_sample(sample_path, sample)
        print(
            f"Random full-text sample: kept={len(sample):,} seed={args.sample_seed} path={sample_path}",
            flush=True,
        )

    return scanned, found


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default=str(PROJECT_ROOT / "法律法规文件库.json"))
    parser.add_argument(
        "--candidates",
        default=str(PROJECT_ROOT / "outputs/policy_packages/new_energy_vehicle/candidates.jsonl"),
    )
    parser.add_argument(
        "--output",
        default=str(PROJECT_ROOT / "outputs/policy_packages/new_energy_vehicle_fulltext/candidates_fulltext.jsonl"),
    )
    parser.add_argument("--json-prefix", default="item")
    parser.add_argument("--progress-every", type=int, default=100000)
    parser.add_argument("--found-progress-every", type=int, default=500)
    parser.add_argument("--sample-size", type=int, default=100)
    parser.add_argument(
        "--sample-output",
        default=str(PROJECT_ROOT / "outputs/policy_packages/new_energy_vehicle_fulltext/candidates_random100_fulltext.jsonl"),
    )
    parser.add_argument("--sample-seed", type=int, default=20260610)
    return parser.parse_args()


def main() -> None:
    build_fulltext_candidates(parse_args())


if __name__ == "__main__":
    main()
