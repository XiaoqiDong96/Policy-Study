#!/usr/bin/env python3
"""Build event-level CAST conference-guide records with availability evidence.

The worker parses the downloaded 2021 DOC and the official 2025 Chinese PDF.
Official totals for 2022--2024 are retained as source-level evidence only; they
are never spread across cities.  A missing event location remains missing.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from priority_worker_utils import (
    PROJECT, ConservativeCityLocator, blank_297_grid, compact, load_cities,
    read_csv, stable_id, write_csv,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_full_collection_text_index import extract_text  # noqa: E402


EVENTS = PROJECT / "01_source_register" / "download_events.csv"
PLANNED = PROJECT / "05_intermediate" / "cast_conference_guide_planned_events.csv"
AVAILABILITY = PROJECT / "10_qc" / "cast_conference_guide_availability.csv"
UNMATCHED = PROJECT / "10_qc" / "cast_conference_guide_location_unmatched.csv"
PANEL = PROJECT / "06_panel" / "cast_conference_guides_297_city_year_2012_2026.csv"
SUMMARY = PROJECT / "10_qc" / "cast_conference_guides_summary.json"

PDF_2025_URL = "https://sj.cast.org.cn/cms_files/filemanager/583933374/attach/202512/7a7b4c5b319f4faf977fae86aa6727e8.pdf"
PDF_2025_FILE = PROJECT / "03_external_raw" / "cast_conference_directory_2025" / "official_2025_chinese_directory.pdf"
OFFICIAL_YEARS = {
    2021: (707, "https://www.cast.org.cn/xs/TZGG/art/2021/art_3680e722538148509cad9d6c038e67be.html"),
    2022: (765, "https://www.cast.org.cn/xw/KXYW/art/2022/art_e26f00192b244792a8491280244583c8.html"),
    2023: (1060, "https://sj.cast.org.cn/xs/TZGG/art/2023/art_9b9bb5b7603c4d1fa941c9a0c31b92a4.html"),
    2024: (978, "https://sj.cast.org.cn/xw/GZDT/art/2024/art_985558314.html"),
    2025: (708, "https://www.cast.org.cn/xw/tzgg/ZH/art/2025/art_bbbba597531d4351bf3d5c7840e3a4e7.html"),
}

PLANNED_FIELDS = [
    "planned_event_id", "guide_year", "sequence_no", "event_name",
    "recommending_organization", "organization_code", "guide_section",
    "city_code", "city_name", "province_code", "province_name",
    "city_match_method", "city_match_evidence", "location_availability",
    "source_url", "source_file", "source_sha256", "extraction_method",
    "formal_variable_eligible",
]

AVAILABILITY_FIELDS = [
    "guide_year", "official_reported_total", "event_level_rows_parsed",
    "event_level_list_status", "city_location_field_status", "source_url",
    "source_file", "source_sha256", "evidence_note",
]

UNMATCHED_FIELDS = [
    "planned_event_id", "guide_year", "sequence_no", "event_name",
    "recommending_organization", "source_url", "source_file", "reason",
]

PANEL_FIELDS = [
    "city_code", "city_name", "province_code", "province_name", "year",
    "cast_guide_event_list_covered_year", "cast_guide_city_located_event_count",
    "cast_guide_unique_recommending_organization_count",
]


def fetch_2025_pdf() -> tuple[Path | None, str, str]:
    PDF_2025_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not PDF_2025_FILE.is_file() or PDF_2025_FILE.stat().st_size < 10_000:
        request = urllib.request.Request(
            PDF_2025_URL,
            headers={"User-Agent": "Mozilla/5.0 (compatible; public-research-data/1.0)"},
        )
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                payload = response.read()
            if not payload.startswith(b"%PDF"):
                raise ValueError("official attachment did not return a PDF")
            temporary = PDF_2025_FILE.with_suffix(".pdf.tmp")
            temporary.write_bytes(payload)
            temporary.replace(PDF_2025_FILE)
        except Exception as exc:
            return None, "", f"download_failed:{type(exc).__name__}:{exc}"
    sha = hashlib.sha256(PDF_2025_FILE.read_bytes()).hexdigest()
    return PDF_2025_FILE, sha, ""


def parse_2021(text: str) -> list[dict[str, Any]]:
    starts = list(re.finditer(r"\|\s*(\d{1,4})\s*\|", text))
    records: list[dict[str, Any]] = []
    for index, start in enumerate(starts):
        number = int(start.group(1))
        if not 1 <= number <= 2_000:
            continue
        end = starts[index + 1].start() if index + 1 < len(starts) else len(text)
        tokens = [compact(value) for value in text[start.start():end].split("|")]
        if len(tokens) < 5 or tokens[1] != str(number):
            continue
        title_parts = [tokens[2]] if tokens[2] else []
        organization_parts = [tokens[3]] if tokens[3] else []
        organization_code = tokens[4]
        continuation = tokens[5:]
        for offset in range(0, len(continuation), 5):
            group = continuation[offset:offset + 5]
            if len(group) >= 3 and group[2]:
                title_parts.append(group[2])
            if len(group) >= 4 and group[3]:
                organization_parts.append(group[3])
        event_name = "".join(title_parts)
        organization = "".join(organization_parts)
        if event_name and organization and re.fullmatch(r"[A-Z]-\d{2}[A-Z]?", organization_code):
            records.append({
                "guide_year": 2021, "sequence_no": number,
                "event_name": event_name, "recommending_organization": organization,
                "organization_code": organization_code, "guide_section": "",
                "extraction_method": "antiword_pipe_table",
            })
    unique = {int(row["sequence_no"]): row for row in records}
    return [unique[number] for number in sorted(unique)]


def parse_2025_pdf(path: Path) -> list[dict[str, Any]]:
    import pdfplumber

    records: dict[int, dict[str, Any]] = {}
    with pdfplumber.open(path) as document:
        for page in document.pages:
            words = page.extract_words(use_text_flow=False, keep_blank_chars=False)
            organization_x_candidates = [
                round(float(word["x0"]), 1)
                for word in words
                if float(word["x0"]) > 350
                and re.search(r"(?:学会|协会|研究会|联合会|委员会|基金会|中心)$", compact(word["text"]))
            ]
            organization_x = Counter(organization_x_candidates).most_common(1)[0][0] - 1 if organization_x_candidates else 409
            anchors: list[tuple[int, float]] = []
            for word in words:
                value = compact(word["text"])
                if word["x0"] < 68 and value.isdigit() and 1 <= int(value) <= 708:
                    anchors.append((int(value), float(word["top"])))
            if not anchors:
                continue
            assigned: dict[int, dict[str, list[tuple[float, float, str]]]] = {
                number: {"title": [], "organization": []} for number, _ in anchors
            }
            for word in words:
                x0, top = float(word["x0"]), float(word["top"])
                if x0 < 68 or top > 765:
                    continue
                number, distance = min(((number, abs(top - anchor_top)) for number, anchor_top in anchors), key=lambda item: item[1])
                if distance > 18:
                    continue
                target = "organization" if x0 >= organization_x else "title"
                assigned[number][target].append((top, x0, compact(word["text"])))
            for number, parts in assigned.items():
                title = "".join(value for _, _, value in sorted(parts["title"]))
                organization = "".join(value for _, _, value in sorted(parts["organization"]))
                if title and organization:
                    records[number] = {
                        "guide_year": 2025, "sequence_no": number,
                        "event_name": title, "recommending_organization": organization,
                        "organization_code": "", "guide_section": "",
                        "extraction_method": "pdfplumber_word_coordinates",
                    }
    return [records[number] for number in sorted(records)]


def add_city_fields(rows: list[dict[str, Any]], locator: ConservativeCityLocator, source: dict[str, str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    output: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    for record in rows:
        matches = locator.locate(record["event_name"], allow_title_alias=True)
        common = {
            **record,
            "source_url": source.get("url", ""), "source_file": source.get("file", ""),
            "source_sha256": source.get("sha256", ""),
        }
        event_id = stable_id("cast_guide", record["guide_year"], record["sequence_no"], record["event_name"], record["recommending_organization"])
        if not matches:
            output.append({
                **common, "planned_event_id": event_id, "location_availability": "not_provided_or_not_explicit_in_event_name",
                "formal_variable_eligible": 0,
            })
            unmatched.append({
                **common, "planned_event_id": event_id,
                "reason": "official_guide_has_no_structured_city_field_and_title_has_no_explicit_297_city",
            })
            continue
        for city, method, evidence in matches:
            output.append({
                **common, "planned_event_id": event_id,
                "city_code": city["city_code"], "city_name": city["city_name"],
                "province_code": city["province_code"], "province_name": city["province_name"],
                "city_match_method": method, "city_match_evidence": evidence,
                "location_availability": "explicit_in_event_name",
                "formal_variable_eligible": 1,
            })
    return output, unmatched


def main() -> int:
    cities = load_cities()
    locator = ConservativeCityLocator(cities)
    events = read_csv(EVENTS)
    planned_rows: list[dict[str, Any]] = []
    unmatched_rows: list[dict[str, Any]] = []
    parsed_counts: dict[int, int] = defaultdict(int)
    availability_errors: dict[int, str] = {}
    sources: dict[int, dict[str, str]] = {}

    event_2021 = next((
        row for row in events
        if row.get("source_id") == "cast_conference_guide_2021"
        and row.get("saved_path", "").lower().endswith(".doc")
        and row.get("http_status", "").startswith("2")
    ), None)
    if event_2021 and (PROJECT / event_2021["saved_path"]).is_file():
        path = PROJECT / event_2021["saved_path"]
        text, method = extract_text(path, event_2021.get("content_type", ""))
        source = {"url": event_2021.get("url", ""), "file": event_2021["saved_path"], "sha256": event_2021.get("sha256", "")}
        rows = parse_2021(text)
        for row in rows:
            row["extraction_method"] = method + "_pipe_table"
        enriched, unmatched = add_city_fields(rows, locator, source)
        planned_rows.extend(enriched)
        unmatched_rows.extend(unmatched)
        parsed_counts[2021] = len({row["sequence_no"] for row in rows})
        sources[2021] = source
    else:
        availability_errors[2021] = "downloaded_2021_doc_missing"

    pdf_path, pdf_sha, pdf_error = fetch_2025_pdf()
    if pdf_path:
        source = {"url": PDF_2025_URL, "file": str(pdf_path.relative_to(PROJECT)), "sha256": pdf_sha}
        rows = parse_2025_pdf(pdf_path)
        enriched, unmatched = add_city_fields(rows, locator, source)
        planned_rows.extend(enriched)
        unmatched_rows.extend(unmatched)
        parsed_counts[2025] = len({row["sequence_no"] for row in rows})
        sources[2025] = source
    else:
        availability_errors[2025] = pdf_error

    availability_rows: list[dict[str, Any]] = []
    for year, (official_total, official_url) in OFFICIAL_YEARS.items():
        parsed = parsed_counts.get(year, 0)
        availability_rows.append({
            "guide_year": year, "official_reported_total": official_total,
            "event_level_rows_parsed": parsed,
            "event_level_list_status": "parsed_complete" if parsed == official_total else "official_aggregate_only" if parsed == 0 else "parsed_count_mismatch",
            "city_location_field_status": "no_structured_city_field;only_explicit_title_locations_mapped" if parsed else "not_available_at_event_level",
            "source_url": official_url, "source_file": sources.get(year, {}).get("file", ""),
            "source_sha256": sources.get(year, {}).get("sha256", ""),
            "evidence_note": availability_errors.get(year, "official total is source-level and is not allocated to cities"),
        })

    covered_years = {year for year, count in parsed_counts.items() if count == OFFICIAL_YEARS[year][0]}
    bucket: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in planned_rows:
        if row.get("city_code"):
            bucket[(row["city_code"], int(row["guide_year"]))].append(row)
    aggregate: dict[tuple[str, int], dict[str, Any]] = {}
    for key, rows in bucket.items():
        aggregate[key] = {
            "cast_guide_city_located_event_count": len({row["planned_event_id"] for row in rows}),
            "cast_guide_unique_recommending_organization_count": len({row["recommending_organization"] for row in rows}),
        }
    panel_rows = blank_297_grid(
        cities, covered_years, aggregate, PANEL_FIELDS[6:],
        "cast_guide_event_list_covered_year",
    )

    write_csv(PLANNED, planned_rows, PLANNED_FIELDS)
    write_csv(AVAILABILITY, availability_rows, AVAILABILITY_FIELDS)
    write_csv(UNMATCHED, unmatched_rows, UNMATCHED_FIELDS)
    write_csv(PANEL, panel_rows, PANEL_FIELDS)
    complete_years = sorted(year for year, total in OFFICIAL_YEARS.items() if parsed_counts.get(year) == total[0])
    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "PASS_WITH_DOCUMENTED_AVAILABILITY_GAPS" if 2021 in complete_years and 2025 in complete_years else "FAIL",
        "official_totals": {str(year): total for year, (total, _) in OFFICIAL_YEARS.items()},
        "event_level_rows_by_year": {str(year): parsed_counts.get(year, 0) for year in OFFICIAL_YEARS},
        "complete_event_level_years": complete_years,
        "event_level_unique_records": len({row["planned_event_id"] for row in planned_rows}),
        "city_located_unique_records": len({row["planned_event_id"] for row in planned_rows if row.get("city_code")}),
        "location_unavailable_records": len({row["planned_event_id"] for row in unmatched_rows}),
        "panel_rows": len(panel_rows),
        "hard_gate": {
            "official_aggregate_totals_not_allocated_to_cities": True,
            "location_missing_not_encoded_as_city_zero": True,
            "event_level_complete_years_match_official_sequence_total": complete_years,
        },
    }
    SUMMARY.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["status"].startswith("PASS") else 1


if __name__ == "__main__":
    raise SystemExit(main())
