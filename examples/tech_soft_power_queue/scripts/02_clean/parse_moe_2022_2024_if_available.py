#!/usr/bin/env python3
"""CR13: conditionally parse 2022--2024 MOE project details.

Exit 78 is used only after CR12 has produced a PASS audit whose decision is
``UNAVAILABLE_WITH_EVIDENCE``.  In that case this worker emits an explicit
all-blank 297-city panel and skip evidence; it never converts unavailability
to zero projects.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from remaining_worker_common import (
    CityMatcher,
    clean,
    panel_base,
    pdf_text,
    read_cities,
    read_csv,
    split_numbered_rows,
    stable_id,
    utc_now,
    write_csv,
    write_json,
)


PROJECT = Path(__file__).resolve().parents[2]
AUDIT = Path("10_qc/moe_2022_2024_endpoint_audit.json")
RECORDS = Path("05_intermediate/moe_2022_2024_project_records.csv")
PANEL = Path("06_panel/moe_2022_2024_collaboration_297_city_year.csv")
SUMMARY = Path("10_qc/moe_2022_2024_conditional_parse_summary.json")
SKIP_EVIDENCE = Path("10_qc/moe_2022_2024_conditional_skip.json")

FIELDS = [
    "record_id", "project_year", "project_id", "company_name", "project_type",
    "project_name", "university_name", "project_leader", "city_code", "city_name",
    "province_code", "province_name", "city_match_status", "source_file",
    "source_parse_method", "raw_record", "qc_flags",
]

ALIASES = {
    "project_id": ("项目编号", "项目编码"),
    "company_name": ("公司名称", "企业名称", "立项企业名称", "支持公司"),
    "project_type": ("项目类型", "类型"),
    "project_name": ("项目名称", "课题名称"),
    "university_name": ("承担学校", "承担高校", "学校名称", "高校名称"),
    "project_leader": ("项目负责人", "负责人"),
}


def xlsx_rows(path: Path) -> list[list[str]]:
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    with zipfile.ZipFile(path) as archive:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in root.findall(f"{ns}si"):
                shared.append("".join(node.text or "" for node in item.iter(f"{ns}t")))
        sheet_names = sorted(name for name in archive.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", name))
        result: list[list[str]] = []
        for sheet_name in sheet_names:
            root = ET.fromstring(archive.read(sheet_name))
            for row in root.iter(f"{ns}row"):
                values: dict[int, str] = {}
                for cell in row.findall(f"{ns}c"):
                    ref = cell.attrib.get("r", "A1")
                    letters = re.match(r"[A-Z]+", ref)
                    column = 0
                    for letter in letters.group() if letters else "A":
                        column = column * 26 + ord(letter) - 64
                    value_node = cell.find(f"{ns}v")
                    inline = cell.find(f"{ns}is")
                    value = value_node.text if value_node is not None else ""
                    if cell.attrib.get("t") == "s" and value.isdigit():
                        value = shared[int(value)]
                    elif inline is not None:
                        value = "".join(node.text or "" for node in inline.iter(f"{ns}t"))
                    values[column - 1] = clean(value)
                if values:
                    result.append([values.get(index, "") for index in range(max(values) + 1)])
        return result


def delimited_rows(path: Path) -> list[list[str]]:
    for encoding in ("utf-8-sig", "gb18030", "utf-8"):
        try:
            with path.open(encoding=encoding, newline="") as handle:
                dialect = csv.Sniffer().sniff(handle.read(4096), delimiters=",\t;")
                handle.seek(0)
                return [[clean(value) for value in row] for row in csv.reader(handle, dialect)]
        except (UnicodeDecodeError, csv.Error):
            continue
    raise RuntimeError(f"Could not decode delimited project file: {path}")


def header_map(row: list[str]) -> dict[str, int]:
    result: dict[str, int] = {}
    for field, aliases in ALIASES.items():
        for index, value in enumerate(row):
            if any(alias in clean(value) for alias in aliases):
                result[field] = index
                break
    return result


def tabular_records(path: Path) -> list[dict[str, str]]:
    rows = xlsx_rows(path) if path.suffix.lower() == ".xlsx" else delimited_rows(path)
    for index, row in enumerate(rows):
        mapping = header_map(row)
        if "university_name" in mapping and ("company_name" in mapping or "project_name" in mapping):
            output = []
            for values in rows[index + 1 :]:
                record = {
                    field: clean(values[column]) if column < len(values) else ""
                    for field, column in mapping.items()
                }
                if record.get("university_name") and (record.get("company_name") or record.get("project_name")):
                    output.append(record)
            return output
    return []


def university_city_map(project: Path) -> dict[str, dict[str, str]]:
    path = project / "05_intermediate" / "official_lists_records.csv"
    result: dict[str, dict[str, str]] = {}
    if not path.is_file():
        return result
    for row in read_csv(path):
        if not row.get("source_id", "").startswith("moe_universities"):
            continue
        if row.get("usable_for_city_panel") != "1":
            continue
        name = clean(row.get("entity_name"))
        if name:
            result[name] = {
                "city_code": row.get("prefecture_code", ""),
                "city_name": row.get("prefecture_name", ""),
                "province_code": row.get("province_code", ""),
                "province_name": row.get("province_name", ""),
            }
    return result


def write_blank_panel(project: Path, cities: list[dict[str, str]]) -> None:
    panel = panel_base(cities)
    for row in panel:
        row["moe_2022_2024_source_observed"] = 0
        row["moe_2022_2024_project_count"] = ""
        row["moe_2022_2024_unique_company_count"] = ""
        row["moe_2022_2024_availability_skipped"] = 1
    write_csv(project / PANEL, panel, list(panel[0]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=PROJECT)
    args = parser.parse_args()
    project = args.project_root.resolve()
    cities = read_cities(project)
    audit_path = project / AUDIT
    if not audit_path.is_file():
        print(f"WAITING_EXTERNAL: CR12 audit missing: {audit_path}")
        return 75
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    if audit.get("status") != "PASS":
        raise RuntimeError("CR12 audit does not have PASS status")
    decision = audit.get("availability_decision")
    paths = [project / path for path in audit.get("public_project_detail_paths", [])]
    if decision == "UNAVAILABLE_WITH_EVIDENCE":
        write_csv(project / RECORDS, [], FIELDS)
        write_blank_panel(project, cities)
        skip = {
            "status": "PASS",
            "task_id": "CR13",
            "generated_at_utc": utc_now(),
            "conditional_evidence_complete": True,
            "outcome": "SKIPPED_WITH_EVIDENCE",
            "reason": "CR12 found no unauthenticated official project-detail file for 2022--2024",
            "cr12_audit": str(AUDIT),
            "availability_decision": decision,
            "panel_rows": 4455,
            "zero_treatment": "all project-count cells blank; unavailability is not zero",
        }
        write_json(project / SKIP_EVIDENCE, skip)
        write_json(project / SUMMARY, skip)
        print(skip)
        return 78
    if decision != "PUBLIC_DETAIL_AVAILABLE" or not paths:
        raise RuntimeError(f"Unsupported CR12 decision or empty file list: {decision!r}")
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise RuntimeError(f"CR12-listed project files are missing: {missing}")

    university_map = university_city_map(project)
    city_matcher = CityMatcher(cities)
    output: list[dict[str, Any]] = []
    observed_years: set[int] = set()
    for path in paths:
        parsed: list[dict[str, str]] = []
        method = ""
        if path.suffix.lower() in {".csv", ".tsv", ".xlsx"}:
            parsed = tabular_records(path)
            method = "structured_table"
        elif path.suffix.lower() == ".pdf":
            text, method = pdf_text(path)
            for raw in split_numbered_rows(text):
                universities = [name for name in university_map if name in raw]
                if universities:
                    parsed.append({"university_name": universities[0], "project_name": raw[:300], "raw_record": raw})
        if not parsed:
            raise RuntimeError(f"Official public detail file could not be parsed into project rows: {path}")
        for record in parsed:
            combined = " ".join(record.values())
            project_id = clean(record.get("project_id"))
            match = re.search(r"20(?:22|23|24)", project_id + " " + combined)
            year = int(match.group()) if match else 0
            if not year:
                raise RuntimeError(f"Project year cannot be established for {path}: {record}")
            observed_years.add(year)
            university = clean(record.get("university_name"))
            city = university_map.get(university)
            if not city:
                matches = city_matcher.all(university + " " + combined)
                city = matches[0] if len(matches) == 1 else None
            output.append({
                "record_id": stable_id(project_id or path, university, record.get("project_name"), year),
                "project_year": year,
                "project_id": project_id,
                "company_name": clean(record.get("company_name")),
                "project_type": clean(record.get("project_type")),
                "project_name": clean(record.get("project_name")),
                "university_name": university,
                "project_leader": clean(record.get("project_leader")),
                "city_code": city.get("city_code", "") if city else "",
                "city_name": city.get("city_name", "") if city else "",
                "province_code": city.get("province_code", "") if city else "",
                "province_name": city.get("province_name", "") if city else "",
                "city_match_status": "matched" if city else "unmatched",
                "source_file": str(path.relative_to(project)),
                "source_parse_method": method,
                "raw_record": clean(record.get("raw_record", "")),
                "qc_flags": "" if city else "university_city_unmatched",
            })
    unique = {str(row["record_id"]): row for row in output}
    output = list(unique.values())
    write_csv(project / RECORDS, output, FIELDS)
    counts: Counter[tuple[str, int]] = Counter()
    companies: dict[tuple[str, int], set[str]] = {}
    for row in output:
        if row["city_code"]:
            key = (str(row["city_code"]), int(row["project_year"]))
            counts[key] += 1
            companies.setdefault(key, set()).add(str(row["company_name"]))
    panel = panel_base(cities)
    for row in panel:
        key = (str(row["city_code"]), int(row["year"]))
        observed = int(row["year"]) in observed_years
        row["moe_2022_2024_source_observed"] = int(observed)
        row["moe_2022_2024_project_count"] = counts[key] if observed else ""
        row["moe_2022_2024_unique_company_count"] = len(companies.get(key, set())) if observed else ""
        row["moe_2022_2024_availability_skipped"] = 0
    write_csv(project / PANEL, panel, list(panel[0]))
    summary = {
        "status": "PASS", "task_id": "CR13", "generated_at_utc": utc_now(),
        "conditional_evidence_complete": True,
        "outcome": "PARSED_PUBLIC_OFFICIAL_DETAIL", "records": len(output),
        "matched_city_records": sum(bool(row["city_code"]) for row in output),
        "observed_years": sorted(observed_years), "panel_rows": len(panel),
        "city_count": len({row["city_code"] for row in panel}),
    }
    write_json(project / SUMMARY, summary)
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
