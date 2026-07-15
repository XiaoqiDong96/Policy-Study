#!/usr/bin/env python3
"""Merge every completed city-year construct onto one 297-city candidate grid.

This is a lossless research staging table, not a finished index.  Missing values
remain missing, duplicate keys fail, and conflicting non-missing values are
reported rather than silently overwritten.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT = Path(__file__).resolve().parents[2]
CITIES = PROJECT / "04_crosswalk" / "city_master_297_snapshot.csv"
OUT = PROJECT / "06_panel" / "full_candidate_297_city_year_2012_2026.csv"
REGISTRY = PROJECT / "04_crosswalk" / "variable_dictionary" / "full_candidate_variable_registry.csv"
QC = PROJECT / "10_qc" / "full_candidate_panel_qc.json"
CONFLICTS = PROJECT / "10_qc" / "full_candidate_merge_conflicts.csv"
YEARS = range(2012, 2027)

INPUTS = [
    ("national", "06_panel/national_297_city_year_2012_2023.csv", "mixed_preclassified"),
    ("listed_jobs", "06_panel/listed_jobs_297_city_year.csv", "extension_only"),
    ("enterprise_thematic", "06_panel/enterprise_thematic_297_city_year_2012_2024.csv", "extension_only"),
    ("patent_partner_network", "06_panel/patent_partner_network_proxy_297_city_year_2012_2024.csv", "soft_connection_proxy"),
    ("listed_procurement", "06_panel/listed_company_government_procurement_297_city_year.csv", "extension_only"),
    ("official_city_designations", "06_panel/official_city_designations_297_city_year_2012_2026.csv", "soft_candidate"),
    ("cast_academic_events", "06_panel/cast_academic_events_297_city_year_2012_2026.csv", "soft_candidate"),
    ("moe_industry_university", "06_panel/moe_industry_university_297_city_year_2014_2026.csv", "soft_candidate"),
    ("university_ecosystem", "06_panel/university_ecosystem_297_city_year_2012_2026.csv", "mixed_soft_environment"),
    ("priority_coworking", "06_panel/priority_coworking_297_city_year_2012_2026.csv", "soft_candidate"),
    ("science_communication_official", "06_panel/priority_cr02_297_city_year_2012_2026.csv", "soft_science_communication_candidate"),
    ("inclusive_technology_official", "06_panel/priority_cr03_297_city_year_2012_2026.csv", "soft_inclusive_human_care_candidate"),
    ("culture_history_official", "06_panel/priority_cr04_297_city_year_2012_2026.csv", "soft_cultural_history_candidate"),
    ("industry_and_ip_official", "06_panel/priority_cr05_297_city_year_2012_2026.csv", "mixed_hard_control_and_institutional_candidate"),
    ("moe_collaboration_edges", "06_panel/moe_collaboration_edges_297_city_year_2012_2026.csv", "soft_connection_candidate"),
    ("cast_conference_guides", "06_panel/cast_conference_guides_297_city_year_2012_2026.csv", "soft_reputation_candidate"),
    ("cast_conference_verified_network", "06_panel/cast_conference_verified_network_297_city_year_2012_2026.csv", "soft_network_candidate"),
    ("green_human_archive", "06_panel/priority_cr09_297_city_year_2012_2026.csv", "green_human_context_candidate"),
    ("industrial_heritage", "06_panel/industrial_heritage_297_city_year_2012_2026.csv", "soft_cultural_history_candidate"),
    ("recognition_lifecycle", "06_panel/recognition_lifecycle_297_city_year_2012_2026.csv", "soft_ecosystem_lifecycle_candidate"),
    ("moe_2022_2024_conditional", "06_panel/moe_2022_2024_collaboration_297_city_year.csv", "soft_connection_candidate_with_availability_flag"),
    ("policy_quality_six_topics", "06_panel/policy_quality_six_topic_297_city_year_2012_2026.csv", "soft_policy_quality_candidate"),
    ("future_policy_domains", "06_panel/future_policy_domains_297_city_year_2012_2026.csv", "soft_candidate"),
    ("culture_industry_policy", "06_panel/culture_industry_policy_297_city_year_2012_2026.csv", "soft_candidate"),
]

CORE = ["year", "city_code", "city_name", "prefecture_type", "province_code", "province_name", "pinyin"]
SKIP = {
    "year", "city_code", "city_name", "prefecture_code", "prefecture_name",
    "prefecture_type", "province_code", "province_name", "province", "pinyin", "task",
}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames or []
        duplicates = sorted({field for field in fields if fields.count(field) > 1})
        if duplicates:
            raise RuntimeError(f"Duplicate column names in {path}: {duplicates}")
        return list(reader)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def norm_code(value: str) -> str:
    text = str(value or "").strip()
    if text.endswith(".0"):
        text = text[:-2]
    return text.zfill(6) if text.isdigit() else text


def equivalent(left: str, right: str) -> bool:
    if left == right:
        return True
    try:
        a, b = float(left), float(right)
        return math.isclose(a, b, rel_tol=1e-10, abs_tol=1e-10)
    except (TypeError, ValueError):
        return False


def load_national_registry() -> dict[str, dict[str, str]]:
    path = PROJECT / "04_crosswalk" / "variable_dictionary" / "national_297_variable_registry.csv"
    if not path.is_file():
        return {}
    return {row["variable"]: row for row in read_csv(path)}


def main() -> int:
    cities = read_csv(CITIES)
    if len(cities) != 297 or len({norm_code(row["city_code"]) for row in cities}) != 297:
        raise RuntimeError("City universe is not exactly 297 unique city codes")
    city_map = {norm_code(row["city_code"]): row for row in cities}
    panel: dict[tuple[str, int], dict[str, str]] = {}
    for code, city in city_map.items():
        for year in YEARS:
            panel[(code, year)] = {
                "year": str(year), "city_code": code, "city_name": city["city_name"],
                "prefecture_type": city["prefecture_type"],
                "province_code": norm_code(city["province_code"]),
                "province_name": city["province_name"], "pinyin": city["pinyin"],
            }

    variable_sources: dict[str, list[str]] = defaultdict(list)
    variable_roles: dict[str, str] = {}
    source_stats: dict[str, Any] = {}
    conflicts: list[dict[str, str]] = []
    field_order: list[str] = list(CORE)

    for source, relative, default_role in INPUTS:
        path = PROJECT / relative
        if not path.is_file():
            source_stats[source] = {"status": "missing_input", "path": relative}
            continue
        rows = read_csv(path)
        seen: set[tuple[str, int]] = set()
        matched = out_of_grid = 0
        source_fields: list[str] = []
        for row in rows:
            code = norm_code(row.get("city_code") or row.get("prefecture_code") or "")
            try:
                year = int(float(row.get("year", "")))
            except ValueError:
                out_of_grid += 1
                continue
            key = (code, year)
            if key not in panel:
                out_of_grid += 1
                continue
            if key in seen:
                raise RuntimeError(f"Duplicate key in {source}: {code}, {year}")
            seen.add(key)
            matched += 1
            for variable, value in row.items():
                if variable in SKIP:
                    continue
                value = "" if value is None else str(value).strip()
                if variable not in source_fields:
                    source_fields.append(variable)
                if variable not in field_order:
                    field_order.append(variable)
                if source not in variable_sources[variable]:
                    variable_sources[variable].append(source)
                variable_roles.setdefault(variable, default_role)
                if not value:
                    continue
                prior = panel[key].get(variable, "")
                if prior and not equivalent(prior, value):
                    conflicts.append({
                        "city_code": code, "year": str(year), "variable": variable,
                        "kept_value": prior, "conflicting_value": value, "incoming_source": source,
                    })
                    continue
                panel[key][variable] = value
        source_stats[source] = {
            "status": "merged", "path": relative, "sha256": sha256_file(path),
            "input_rows": len(rows), "matched_grid_rows": matched,
            "out_of_grid_rows": out_of_grid, "variables_seen": len(source_fields),
        }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=field_order, extrasaction="ignore")
        writer.writeheader()
        for key in sorted(panel, key=lambda item: (item[1], item[0])):
            writer.writerow(panel[key])

    national_registry = load_national_registry()
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    with REGISTRY.open("w", encoding="utf-8-sig", newline="") as handle:
        fields = ["variable", "source_panels", "candidate_role", "national_dimension", "national_decision", "nonmissing_rows"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for variable in field_order[len(CORE):]:
            prior = national_registry.get(variable, {})
            writer.writerow({
                "variable": variable,
                "source_panels": "|".join(variable_sources[variable]),
                "candidate_role": prior.get("index_version") or variable_roles.get(variable, "unclassified"),
                "national_dimension": prior.get("dimension", ""),
                "national_decision": prior.get("decision", ""),
                "nonmissing_rows": sum(bool(row.get(variable, "")) for row in panel.values()),
            })

    CONFLICTS.parent.mkdir(parents=True, exist_ok=True)
    with CONFLICTS.open("w", encoding="utf-8-sig", newline="") as handle:
        fields = ["city_code", "year", "variable", "kept_value", "conflicting_value", "incoming_source"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(conflicts)

    missing_inputs = [source for source, stat in source_stats.items() if stat["status"] != "merged"]
    qc = {
        "status": "PASS" if not missing_inputs and not conflicts else "FAIL",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "city_count": 297, "years": [2012, 2026], "rows": len(panel),
        "expected_rows": 297 * 15, "variables_excluding_identifiers": len(field_order) - len(CORE),
        "missing_inputs": missing_inputs, "merge_conflicts": len(conflicts),
        "missing_policy": "preserved; no zero filling or imputation",
        "index_note": "candidate staging panel only; no scaling, weighting, or composite score",
        "source_stats": source_stats,
        "outputs": {
            "panel": str(OUT.relative_to(PROJECT)), "panel_sha256": sha256_file(OUT),
            "registry": str(REGISTRY.relative_to(PROJECT)),
            "conflicts": str(CONFLICTS.relative_to(PROJECT)),
        },
    }
    QC.write_text(json.dumps(qc, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(qc, ensure_ascii=False, indent=2))
    return 0 if qc["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
