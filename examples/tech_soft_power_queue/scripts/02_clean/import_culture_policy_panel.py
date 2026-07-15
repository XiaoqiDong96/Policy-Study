#!/usr/bin/env python3
"""Import the completed culture-industry policy workflow to a 297-city panel.

The policy workflow runs in a separate project.  This importer refuses to use
partial model output: its completion flag must exist and every final-YES policy
must have one tool-refinement row.  Only policies with an explicit prefecture
administrative code are assigned to a city.  Province-level policies are kept
in the document table but are not broadcast to every city in the province.

Zero policy counts are not manufactured.  City-years without an explicitly
mapped local policy remain blank and carry a separate observation flag.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


PROJECT = Path(__file__).resolve().parents[2]
CITIES = PROJECT / "04_crosswalk" / "city_master_297_snapshot.csv"
RAW = PROJECT / "03_external_raw" / "policy_panels" / "culture_industry"
DOCUMENTS = RAW / "culture_industry_policy_documents.csv"
PANEL = PROJECT / "06_panel" / "culture_industry_policy_297_city_year_2012_2026.csv"
QC = PROJECT / "10_qc" / "culture_industry_policy_coverage.csv"
UNMAPPED = PROJECT / "10_qc" / "culture_industry_policy_unmapped_documents.csv"
MANIFEST = PROJECT / "10_qc" / "culture_industry_policy_manifest.json"
CODEBOOK = (
    PROJECT
    / "04_crosswalk"
    / "variable_dictionary"
    / "culture_industry_policy_codebook.csv"
)
YEARS = range(2012, 2027)
SOURCE_TO_CURRENT_CITY_CODE = {
    "542400": "540600",  # historical Nagqu prefecture code
}

POLICY_RELATIVE = Path("outputs/culture_industry_policy_panel")
FINAL_YES_RELATIVE = (
    POLICY_RELATIVE
    / "stage2_dual_vote_boundary"
    / "qwen_full"
    / "final"
    / "culture_industry_dual_vote_final_qwen_yes.jsonl"
)
TOOLS_RELATIVE = (
    POLICY_RELATIVE
    / "tool_refinement"
    / "culture_industry_tool_refined.jsonl"
)
COMPLETE_RELATIVE = POLICY_RELATIVE / "culture_industry_full_workflow_complete.flag"
PROVINCE_SUMMARY_RELATIVE = (
    POLICY_RELATIVE
    / "final_province_category_panels"
    / "culture_industry_policy_province_category_summary.json"
)

DOCUMENT_FIELDS = [
    "id",
    "title",
    "province",
    "pub_depart",
    "law_type",
    "pub_num",
    "pub_date",
    "use_date",
    "date_month",
    "admin_level",
    "admin_province",
    "admin_city",
    "city_adcode",
    "formal_297_city_eligible",
    "measure_specificity",
    "policy_tone",
    "timing",
    "policy_side",
    "strength_score",
    "coverage_breadth_score",
    "policy_tools",
    "tool_groups",
    "target_segments",
    "specific_measures",
    "implementation_mechanisms",
    "tool_confidence",
    "tool_error",
]

PANEL_METRICS = [
    "culture_industry_policy_local_count",
    "culture_industry_policy_specific_measures_count",
    "culture_industry_policy_joint_issuance_count",
    "culture_industry_policy_supply_side_count",
    "culture_industry_policy_demand_side_count",
    "culture_industry_policy_ecosystem_side_count",
    "culture_industry_policy_strength_sum",
    "culture_industry_policy_strength_mean",
    "culture_industry_policy_coverage_breadth_mean",
    "culture_industry_policy_tool_breadth_mean",
    "culture_industry_policy_tool_groups_unique",
    "culture_industry_policy_target_segments_unique",
]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: Iterable[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"Invalid JSONL at {path}:{line_number}: {exc}") from exc
            if not isinstance(value, dict):
                raise RuntimeError(f"Expected object at {path}:{line_number}")
            rows.append(value)
    return rows


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_code(value: Any) -> str:
    text = str(value or "").strip()
    if text.endswith(".0"):
        text = text[:-2]
    if text.isdigit():
        text = text.zfill(6)
    return SOURCE_TO_CURRENT_CITY_CODE.get(text, text)


def as_number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value is None:
        return []
    text = str(value).strip()
    if not text:
        return []
    return [part.strip() for part in text.split("|") if part.strip()]


def is_joint_issuance(pub_depart: Any) -> bool:
    text = str(pub_depart or "")
    separators = (";", "；", "、", "|", "，")
    return any(separator in text for separator in separators)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=PROJECT)
    parser.add_argument(
        "--policy-project",
        type=Path,
        default=Path(os.environ.get("POLICY_PROJECT", "../nev_policy_project")),
    )
    args = parser.parse_args()
    project = args.project_root.resolve()
    policy_project = args.policy_project.resolve()

    final_yes = policy_project / FINAL_YES_RELATIVE
    tools_file = policy_project / TOOLS_RELATIVE
    complete_flag = policy_project / COMPLETE_RELATIVE
    province_summary = policy_project / PROVINCE_SUMMARY_RELATIVE
    if not complete_flag.is_file():
        print(f"WAITING_EXTERNAL: {complete_flag}")
        return 75
    required = [final_yes, tools_file, province_summary]
    missing = [str(path) for path in required if not path.is_file() or not path.stat().st_size]
    if missing:
        raise SystemExit(f"Completed workflow is missing expected files: {missing}")

    cities = read_csv(project / CITIES.relative_to(PROJECT))
    if len(cities) != 297:
        raise RuntimeError(f"Expected 297-city universe, found {len(cities)}")
    city_by_code = {normalize_code(row["city_code"]): row for row in cities}
    if len(city_by_code) != 297:
        raise RuntimeError("City universe does not contain 297 unique codes")

    policies = read_jsonl(final_yes)
    tools = read_jsonl(tools_file)
    policy_ids = [str(row.get("id", "")) for row in policies]
    tool_ids = [str(row.get("id", "")) for row in tools]
    if len(set(policy_ids)) != len(policy_ids):
        raise RuntimeError("Duplicate ids in final YES culture-policy output")
    if len(set(tool_ids)) != len(tool_ids):
        raise RuntimeError("Duplicate ids in culture-policy tool refinement")
    tool_by_id = {str(row.get("id", "")): row for row in tools}
    missing_tool_ids = sorted(set(policy_ids) - set(tool_by_id))
    extra_tool_ids = sorted(set(tool_by_id) - set(policy_ids))
    if missing_tool_ids or extra_tool_ids or len(tools) != len(policies):
        raise RuntimeError(
            "Culture-policy tool refinement does not align one-to-one with final YES: "
            f"policies={len(policies)} tools={len(tools)} "
            f"missing={len(missing_tool_ids)} extra={len(extra_tool_ids)}"
        )

    document_rows: list[dict[str, Any]] = []
    unmapped_rows: list[dict[str, Any]] = []
    cells: dict[tuple[str, int], dict[str, Any]] = defaultdict(
        lambda: {
            "count": 0,
            "specific": 0,
            "joint": 0,
            "side": Counter(),
            "strength": 0.0,
            "breadth": 0.0,
            "tool_breadth": 0.0,
            "tool_groups": set(),
            "segments": set(),
        }
    )
    national_year_counts: Counter[int] = Counter()

    for policy in policies:
        policy_id = str(policy.get("id", ""))
        tool_row = tool_by_id[policy_id]
        classification = tool_row.get("tool_classification") or {}
        if not isinstance(classification, dict):
            classification = {}
        admin = policy.get("admin") or {}
        if not isinstance(admin, dict):
            admin = {}
        code = normalize_code(admin.get("city_adcode"))
        date_month = str(policy.get("date_month") or "")
        year = int(date_month[:4]) if date_month[:4].isdigit() else None
        if year is not None:
            national_year_counts[year] += 1
        formal = bool(
            year in YEARS
            and code in city_by_code
            and str(admin.get("level", "")) == "prefecture"
        )
        policy_tools = as_list(classification.get("policy_tools"))
        tool_groups = as_list(classification.get("tool_groups"))
        target_segments = as_list(classification.get("target_segments"))
        specific_measures = as_list(classification.get("specific_measures"))
        mechanisms = as_list(classification.get("implementation_mechanisms"))
        document = {
            "id": policy_id,
            "title": policy.get("title", ""),
            "province": policy.get("province", ""),
            "pub_depart": policy.get("pub_depart", ""),
            "law_type": policy.get("law_type", ""),
            "pub_num": policy.get("pub_num", ""),
            "pub_date": policy.get("pub_date", ""),
            "use_date": policy.get("use_date", ""),
            "date_month": date_month,
            "admin_level": admin.get("level", ""),
            "admin_province": admin.get("province", ""),
            "admin_city": admin.get("city", ""),
            "city_adcode": code,
            "formal_297_city_eligible": int(formal),
            "measure_specificity": classification.get("measure_specificity", ""),
            "policy_tone": classification.get("policy_tone", ""),
            "timing": classification.get("timing", ""),
            "policy_side": classification.get("policy_side", ""),
            "strength_score": classification.get("strength_score", ""),
            "coverage_breadth_score": classification.get("coverage_breadth_score", ""),
            "policy_tools": "|".join(policy_tools),
            "tool_groups": "|".join(tool_groups),
            "target_segments": "|".join(target_segments),
            "specific_measures": "|".join(specific_measures),
            "implementation_mechanisms": "|".join(mechanisms),
            "tool_confidence": classification.get("tool_confidence", ""),
            "tool_error": tool_row.get("tool_error", ""),
        }
        document_rows.append(document)
        if not formal:
            unmapped_rows.append(document)
            continue

        cell = cells[(code, int(year))]
        cell["count"] += 1
        if classification.get("measure_specificity") == "specific_measures" or specific_measures:
            cell["specific"] += 1
        if is_joint_issuance(policy.get("pub_depart")):
            cell["joint"] += 1
        side = str(classification.get("policy_side") or "")
        if side:
            cell["side"][side] += 1
        cell["strength"] += as_number(classification.get("strength_score"))
        cell["breadth"] += as_number(classification.get("coverage_breadth_score"))
        cell["tool_breadth"] += len(set(policy_tools))
        cell["tool_groups"].update(tool_groups)
        cell["segments"].update(target_segments)

    panel_rows: list[dict[str, Any]] = []
    for city in cities:
        code = normalize_code(city["city_code"])
        for year in YEARS:
            row: dict[str, Any] = {
                "city_code": code,
                "city_name": city["city_name"],
                "province_code": normalize_code(city["province_code"]),
                "province_name": city["province_name"],
                "year": year,
                "culture_industry_policy_national_source_documents": national_year_counts.get(year, 0),
                "culture_industry_policy_local_observed": 0,
            }
            cell = cells.get((code, year))
            if cell:
                count = int(cell["count"])
                row.update(
                    {
                        "culture_industry_policy_local_observed": 1,
                        "culture_industry_policy_local_count": count,
                        "culture_industry_policy_specific_measures_count": int(cell["specific"]),
                        "culture_industry_policy_joint_issuance_count": int(cell["joint"]),
                        "culture_industry_policy_supply_side_count": int(cell["side"].get("supply", 0)),
                        "culture_industry_policy_demand_side_count": int(cell["side"].get("demand", 0)),
                        "culture_industry_policy_ecosystem_side_count": int(cell["side"].get("ecosystem", 0)),
                        "culture_industry_policy_strength_sum": round(cell["strength"], 8),
                        "culture_industry_policy_strength_mean": round(cell["strength"] / count, 8),
                        "culture_industry_policy_coverage_breadth_mean": round(cell["breadth"] / count, 8),
                        "culture_industry_policy_tool_breadth_mean": round(cell["tool_breadth"] / count, 8),
                        "culture_industry_policy_tool_groups_unique": len(cell["tool_groups"]),
                        "culture_industry_policy_target_segments_unique": len(cell["segments"]),
                    }
                )
            else:
                for metric in PANEL_METRICS:
                    row[metric] = ""
            panel_rows.append(row)

    panel_fields = [
        "city_code",
        "city_name",
        "province_code",
        "province_name",
        "year",
        "culture_industry_policy_national_source_documents",
        "culture_industry_policy_local_observed",
        *PANEL_METRICS,
    ]
    qc_rows = []
    for year in YEARS:
        qc_rows.append(
            {
                "year": year,
                "national_final_yes_documents": national_year_counts.get(year, 0),
                "mapped_local_documents": sum(
                    int(cell["count"])
                    for (code, candidate_year), cell in cells.items()
                    if candidate_year == year
                ),
                "cities_with_local_policy": sum(
                    candidate_year == year for code, candidate_year in cells
                ),
                "panel_city_rows": 297,
            }
        )

    output_paths = {
        "documents": project / DOCUMENTS.relative_to(PROJECT),
        "panel": project / PANEL.relative_to(PROJECT),
        "qc": project / QC.relative_to(PROJECT),
        "unmapped": project / UNMAPPED.relative_to(PROJECT),
        "manifest": project / MANIFEST.relative_to(PROJECT),
        "codebook": project / CODEBOOK.relative_to(PROJECT),
    }
    write_csv(output_paths["documents"], document_rows, DOCUMENT_FIELDS)
    write_csv(output_paths["panel"], panel_rows, panel_fields)
    write_csv(
        output_paths["qc"],
        qc_rows,
        [
            "year",
            "national_final_yes_documents",
            "mapped_local_documents",
            "cities_with_local_policy",
            "panel_city_rows",
        ],
    )
    write_csv(output_paths["unmapped"], unmapped_rows, DOCUMENT_FIELDS)
    codebook_rows = [
        {
            "variable": "culture_industry_policy_local_count",
            "construct": "local culture-industry policy activity",
            "index_role": "candidate_policy_soft_power",
            "caution": "blank means no explicitly mapped city policy; it is not silently converted to zero",
        },
        {
            "variable": "culture_industry_policy_specific_measures_count",
            "construct": "culture-policy implementation specificity",
            "index_role": "candidate_policy_quality",
            "caution": "model-assisted tool classification; retain model and evidence provenance",
        },
        {
            "variable": "culture_industry_policy_tool_breadth_mean",
            "construct": "culture-policy instrument breadth",
            "index_role": "candidate_policy_quality",
            "caution": "mean number of distinct classified tools among mapped local policies",
        },
    ]
    write_csv(
        output_paths["codebook"],
        codebook_rows,
        ["variable", "construct", "index_role", "caution"],
    )

    copied_inputs: dict[str, Any] = {}
    raw_dir = project / RAW.relative_to(PROJECT)
    raw_dir.mkdir(parents=True, exist_ok=True)
    for source in (final_yes, tools_file, province_summary, complete_flag):
        target = raw_dir / source.name
        if source.resolve() != target.resolve():
            shutil.copy2(source, target)
        copied_inputs[source.name] = {
            "path": str(target.relative_to(project)),
            "bytes": target.stat().st_size,
            "sha256": sha256_file(target),
        }

    tool_errors = sum(bool(str(row.get("tool_error", "")).strip()) for row in tools)
    manifest = {
        "status": "PASS",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "completion_flag": str(complete_flag),
        "final_yes_documents": len(policies),
        "tool_refinement_rows": len(tools),
        "tool_error_rows_retained": tool_errors,
        "mapped_prefecture_documents": sum(int(row["formal_297_city_eligible"]) for row in document_rows),
        "unmapped_or_nonprefecture_documents": len(unmapped_rows),
        "mapped_city_count": len({code for code, year in cells}),
        "panel_rows": len(panel_rows),
        "expected_panel_rows": 297 * len(YEARS),
        "city_count": 297,
        "years": [min(YEARS), max(YEARS)],
        "zero_policy": "no silent zero filling; local metrics are blank without an explicitly mapped policy",
        "province_policy_rule": "province-level documents retained in document table, never broadcast to all cities",
        "copied_inputs": copied_inputs,
        "outputs": {
            name: str(path.relative_to(project)) for name, path in output_paths.items()
        },
    }
    if len(panel_rows) != 297 * len(YEARS) or tool_errors:
        manifest["status"] = "FAIL"
    output_paths["manifest"].write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0 if manifest["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
