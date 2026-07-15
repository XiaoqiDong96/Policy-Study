#!/usr/bin/env python3
"""CR15: audit yearbook file, year, geography, and definition coverage.

The cloud receives only a local-asset inventory, not the mounted-drive files.
Accordingly this worker can establish inventory coverage but cannot inspect
table contents or silently treat filenames as data.  Extraction is authorized
only when files are accessible and explicit city-year coverage is sufficient.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "02_clean"
sys.path.insert(0, str(SCRIPT_DIR))

from remaining_worker_common import (  # noqa: E402
    panel_base,
    read_cities,
    stable_id,
    utc_now,
    write_csv,
    write_json,
)


PROJECT = Path(__file__).resolve().parents[2]
INVENTORY = Path("01_source_register/local_asset_inventory.csv")
FILES = Path("10_qc/yearbook_inventory_candidate_files.csv")
ROOTS = Path("10_qc/yearbook_inventory_root_coverage.csv")
MATRIX = Path("10_qc/yearbook_inventory_city_year_coverage.csv")
SUMMARY = Path("10_qc/yearbook_coverage_audit.json")

YEARBOOK = re.compile(r"年鉴|统计资料|统计年报|城市统计|统计汇编|统计手册")
RESEARCH_RELEVANT = re.compile(r"科技|科学技术|研发|R&D|教育|文化|环境|环保|工业|高新|人才|创新|信息")
TABLE_SUFFIXES = {".csv", ".xls", ".xlsx", ".dta", ".sav", ".dbf"}
DOCUMENT_SUFFIXES = {".pdf", ".doc", ".docx", ".txt", ".rtf"}


def years_in(value: str) -> set[int]:
    years = {int(year) for year in re.findall(r"(?:19|20)\d{2}", value)}
    for left, right in re.findall(r"((?:19|20)\d{2})\s*[-—至到_]\s*((?:19|20)\d{2})", value):
        start, end = int(left), int(right)
        if start <= end and end - start <= 40:
            years.update(range(start, end + 1))
    return {year for year in years if 2012 <= year <= 2026}


def basename(value: str) -> str:
    return value.replace("\\", "/").rstrip("/").split("/")[-1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=PROJECT)
    parser.add_argument("--inventory", type=Path)
    parser.add_argument("--minimum-city-share", type=float, default=0.70)
    parser.add_argument("--minimum-years", type=int, default=10)
    args = parser.parse_args()
    project = args.project_root.resolve()
    inventory_path = args.inventory or (project / INVENTORY)
    if not inventory_path.is_file():
        print(f"WAITING_EXTERNAL: local asset inventory is absent: {inventory_path}")
        return 75
    cities = read_cities(project)
    city_tokens = sorted(
        [(row["city_name"], row) for row in cities],
        key=lambda item: (-len(item[0]), item[0]),
    )
    with inventory_path.open(encoding="utf-8-sig", newline="") as handle:
        inventory = list(csv.DictReader(handle))
    required_fields = {"root", "path", "exists", "bytes", "suffix", "partial"}
    if not inventory or not required_fields <= set(inventory[0]):
        raise RuntimeError(f"Inventory schema is missing fields {sorted(required_fields)}")

    candidates: list[dict[str, Any]] = []
    coverage: Counter[tuple[str, int]] = Counter()
    root_stats: defaultdict[str, Counter[str]] = defaultdict(Counter)
    root_years: defaultdict[str, set[int]] = defaultdict(set)
    root_cities: defaultdict[str, set[str]] = defaultdict(set)
    accessible_files = 0
    for row in inventory:
        path_raw = row.get("path", "")
        root_raw = row.get("root", "")
        evidence_text = path_raw + " " + root_raw
        if not YEARBOOK.search(evidence_text):
            continue
        years = years_in(evidence_text)
        matched_cities: dict[str, dict[str, str]] = {}
        compact = re.sub(r"\s+", "", evidence_text)
        for token, city in city_tokens:
            if token and token in compact:
                matched_cities[city["city_code"]] = city
        suffix = (row.get("suffix") or Path(path_raw).suffix).lower()
        root_id = stable_id(root_raw, length=16)
        root_label = basename(root_raw)
        path_id = hashlib.sha256(path_raw.encode("utf-8")).hexdigest()
        is_table = int(suffix in TABLE_SUFFIXES)
        is_document = int(suffix in DOCUMENT_SUFFIXES)
        originally_exists = int(row.get("exists", "0") == "1")
        partial = int(row.get("partial", "0") == "1")
        # The inventory's exists flag describes the workstation snapshot; it
        # does not prove the file is mounted on the cloud host.
        cloud_accessible = int(Path(path_raw).is_file())
        accessible_files += cloud_accessible
        candidate = {
            "path_sha256": path_id,
            "root_id": root_id,
            "root_label": root_label,
            "file_name": basename(path_raw),
            "suffix": suffix,
            "bytes": row.get("bytes", ""),
            "inventory_exists": originally_exists,
            "inventory_partial": partial,
            "cloud_file_accessible": cloud_accessible,
            "structured_table_suffix": is_table,
            "document_suffix": is_document,
            "research_topic_hint": int(bool(RESEARCH_RELEVANT.search(evidence_text))),
            "years_detected": "|".join(map(str, sorted(years))),
            "year_count": len(years),
            "city_codes_detected": "|".join(sorted(matched_cities)),
            "city_count": len(matched_cities),
            "geography_evidence_rule": "exact formal city name in inventory path only",
            "content_definition_audited": 0,
            "main_index_eligible": 0,
            "eligibility_reason": "inventory metadata only; file contents not accessible on cloud",
        }
        candidates.append(candidate)
        stats = root_stats[root_id]
        stats["candidate_files"] += 1
        stats["structured_table_files"] += is_table
        stats["document_files"] += is_document
        stats["cloud_accessible_files"] += cloud_accessible
        stats["partial_files"] += partial
        root_years[root_id].update(years)
        root_cities[root_id].update(matched_cities)
        for code in matched_cities:
            for year in years:
                coverage[(code, year)] += 1

    file_fields = [
        "path_sha256", "root_id", "root_label", "file_name", "suffix", "bytes",
        "inventory_exists", "inventory_partial", "cloud_file_accessible",
        "structured_table_suffix", "document_suffix", "research_topic_hint",
        "years_detected", "year_count", "city_codes_detected", "city_count",
        "geography_evidence_rule", "content_definition_audited", "main_index_eligible",
        "eligibility_reason",
    ]
    write_csv(project / FILES, candidates, file_fields)

    root_labels = {row["root_id"]: row["root_label"] for row in candidates}
    root_rows = []
    for root_id, stats in sorted(root_stats.items()):
        years = root_years[root_id]
        city_codes = root_cities[root_id]
        potential_share = len(city_codes) / 297
        sufficient_names = int(potential_share >= args.minimum_city_share and len(years) >= args.minimum_years)
        root_rows.append({
            "root_id": root_id,
            "root_label": root_labels.get(root_id, ""),
            **dict(stats),
            "years_detected": "|".join(map(str, sorted(years))),
            "year_count": len(years),
            "city_count_from_paths": len(city_codes),
            "city_share_from_paths": f"{potential_share:.6f}",
            "filename_coverage_threshold_met": sufficient_names,
            "content_access_and_definition_threshold_met": 0,
            "extraction_decision": "not_extractable_from_cloud_inventory_only",
        })
    root_fields = list(root_rows[0]) if root_rows else [
        "root_id", "root_label", "candidate_files", "structured_table_files",
        "document_files", "cloud_accessible_files", "partial_files", "years_detected",
        "year_count", "city_count_from_paths", "city_share_from_paths",
        "filename_coverage_threshold_met", "content_access_and_definition_threshold_met",
        "extraction_decision",
    ]
    write_csv(project / ROOTS, root_rows, root_fields)

    matrix = panel_base(cities)
    for row in matrix:
        count = coverage[(str(row["city_code"]), int(row["year"]))]
        row["yearbook_inventory_path_file_count"] = count
        row["yearbook_inventory_path_observed"] = int(count > 0)
        row["yearbook_content_verified"] = 0
        row["yearbook_main_index_value"] = ""
        row["yearbook_sensitivity_only"] = 1
    write_csv(project / MATRIX, matrix, list(matrix[0]))

    covered_cities = {code for code, year in coverage if coverage[(code, year)] > 0}
    covered_years = {year for code, year in coverage if coverage[(code, year)] > 0}
    covered_cells = sum(count > 0 for count in coverage.values())
    if not candidates:
        decision_reason = (
            "the cloud inventory contains no file/root labelled as a yearbook, "
            "statistical annual report, or statistical compilation; no values can be extracted"
        )
    elif not accessible_files:
        decision_reason = (
            "inventory proves candidate files existed on the external-drive snapshot, "
            "but cloud cannot inspect contents, definitions, sheets, or missingness"
        )
    else:
        decision_reason = (
            "candidate files are present but filename coverage alone does not satisfy the "
            "required content-definition and sheet audit"
        )
    summary = {
        "status": "PASS",
        "task_id": "CR15",
        "generated_at_utc": utc_now(),
        "audit_complete": True,
        "inventory_rows": len(inventory),
        "yearbook_candidate_files": len(candidates),
        "yearbook_candidate_roots": len(root_rows),
        "structured_table_candidate_files": sum(int(row["structured_table_suffix"]) for row in candidates),
        "cloud_accessible_candidate_files": accessible_files,
        "path_explicit_city_count": len(covered_cities),
        "path_explicit_year_count": len(covered_years),
        "path_explicit_city_year_cells": covered_cells,
        "coverage_matrix_rows": len(matrix),
        "main_index_eligible": False,
        "extraction_performed": False,
        "decision": "SENSITIVITY_ONLY_INVENTORY_EVIDENCE",
        "decision_reason": decision_reason,
        "promotion_rule": {
            "minimum_city_share": args.minimum_city_share,
            "minimum_years": args.minimum_years,
            "requires_cloud_or_mounted_content_access": True,
            "requires_definition_and_sheet_audit": True,
        },
        "zero_rule": "no statistical values emitted; filename non-mention is not a measured zero",
    }
    write_json(project / SUMMARY, summary)
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
