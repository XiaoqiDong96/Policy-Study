#!/usr/bin/env python3
"""CR10: fetch and structure current official national industrial-heritage lists.

The current authoritative lifecycle evidence is the 2024 sixth-batch notice
plus first/second-batch review and rectification attachments, and the 2025
seventh-batch notice plus third-batch review attachment.  The old first,
second, and third notices were explicitly superseded, so this worker does not
treat their old rows as current stock.  Older fourth-batch and fifth-batch
public evidence is retained when downloadable, with provisional fifth-batch
rows clearly excluded from formal designation flows.
"""

from __future__ import annotations

import argparse
import re
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urljoin, urlparse

from remaining_worker_common import (
    clean,
    extract_links,
    fetch_url,
    panel_base,
    pdf_text,
    read_cities,
    sha256_file,
    stable_id,
    utc_now,
    write_csv,
    write_json,
)


PROJECT = Path(__file__).resolve().parents[2]
RAW_DIR = Path("03_external_raw/miit_industrial_heritage_priority")
RECORDS = Path("05_intermediate/industrial_heritage_lifecycle_records.csv")
PANEL = Path("06_panel/industrial_heritage_297_city_year_2012_2026.csv")
SUMMARY = Path("10_qc/industrial_heritage_summary.json")
SOURCES = Path("10_qc/industrial_heritage_source_evidence.csv")
UNMATCHED = Path("10_qc/industrial_heritage_unmatched_records.csv")

PAGE_SPECS = [
    {
        "source_id": "miit_industrial_heritage_2024_current",
        "url": "https://wap.miit.gov.cn/jgsj/zfs/gzdt/art/2024/art_46a8ba5d8aa94c23910407471107a2bf.html",
        "year": 2024,
        "required": True,
    },
    {
        "source_id": "miit_industrial_heritage_2025_current",
        "url": "https://www.miit.gov.cn/zwgk/zcwj/wjfb/tz/art/2025/art_fee42179db1d4469af6bfa5c91506672.html",
        "year": 2025,
        "required": True,
    },
    {
        "source_id": "miit_industrial_heritage_batch4",
        "url": "https://www.miit.gov.cn/zwgk/zcwj/wjfb/tg/art/2020/art_0b5d2011047246ccb08661443e65a077.html",
        "year": 2020,
        "required": False,
    },
]

# These official attachments are kept explicitly because the mobile page can
# render its link list dynamically.  They are the binding 2024 evidence.
DIRECT_SPECS = [
    {
        "source_id": "miit_industrial_heritage_batch6",
        "url": "https://www.miit.gov.cn/cms_files/filemanager/1226211233/attach/202410/818d9972a3d74f9c962eadfcd2ecafa3.pdf",
        "label": "第六批国家工业遗产名单",
        "year": 2024,
        "event_type": "designation",
        "batch": "6",
        "formal": 1,
        "required": True,
    },
    {
        "source_id": "miit_industrial_heritage_review12",
        "url": "https://www.miit.gov.cn/cms_files/filemanager/1226211233/attach/202410/8488a21fbb854fe7a07d08a03adbdc6f.pdf",
        "label": "通过复核的第一二批国家工业遗产名单",
        "year": 2024,
        "event_type": "review_passed",
        "batch": "1|2",
        "formal": 1,
        "required": True,
    },
    {
        "source_id": "miit_industrial_heritage_rectification12",
        "url": "https://www.miit.gov.cn/cms_files/filemanager/1226211233/attach/202410/51646e4202cb4c05a47a0addaaad7185.pdf",
        "label": "需要整改项目名单",
        "year": 2024,
        "event_type": "review_rectification",
        "batch": "1|2",
        "formal": 1,
        "required": True,
    },
    {
        "source_id": "miit_industrial_heritage_batch5_proposed",
        "url": "https://www.miit.gov.cn/cms_files/filemanager/1226211233/attach/202111/a72b8597922d45048817a05c27ccfc65.pdf",
        "label": "拟认定的第五批国家工业遗产名单",
        "year": 2021,
        "event_type": "proposed_not_final",
        "batch": "5",
        "formal": 0,
        "required": False,
    },
]

RECORD_FIELDS = [
    "record_id", "source_id", "event_year", "event_type", "batch",
    "entity_name", "address_evidence", "raw_record", "city_code", "city_name",
    "province_code", "province_name", "match_status", "match_method",
    "formal_flow_eligible", "source_url", "source_file", "source_sha256",
    "extraction_method", "qc_flags",
]

# Two review-table addresses are broken around the PDF row number, so their
# prefecture name is no longer a contiguous string.  The district names are
# unique official administrative evidence and are mapped explicitly.
DISTRICT_CITY_OVERRIDES = {
    "西塞山区": "420200",  # 黄石市
    "杏花岭区": "140100",  # 太原市
}


def classify_attachment(label: str, url: str, year: int) -> dict[str, object] | None:
    text = clean(label + " " + url)
    # MIIT sometimes exposes attachments through an API-gateway URL without a
    # filename suffix; an explicit attachment label is therefore sufficient
    # for discovery, with PDF magic/content type verified after download.
    if not re.search(r"工业遗产", text):
        return None
    if "第三批" in text and "复核" in text:
        return {"event_type": "review_passed", "batch": "3", "formal": 1}
    if "第七批" in text:
        return {"event_type": "designation", "batch": "7", "formal": 1}
    if "第四批" in text and year == 2020:
        return {"event_type": "designation", "batch": "4", "formal": 1}
    return None


def context_rows(text: str) -> list[str]:
    """Keep the address/name lines that often precede a PDF row number."""
    lines = text.replace("\r", "\n").splitlines()
    marks = [index for index, line in enumerate(lines) if re.match(r"^\s*\d{1,3}\s{2,}", line)]
    vertical_table = False
    if len(marks) < 2:
        marks = [index for index, line in enumerate(lines) if re.fullmatch(r"\s*\d{1,3}\s*", line)]
        vertical_table = True
    rows: list[str] = []
    for position, line_index in enumerate(marks):
        start = line_index if vertical_table else max(0, line_index - 3)
        end = marks[position + 1] if position + 1 < len(marks) else len(lines)
        selected = list(lines[start:end])
        if vertical_table:
            selected[line_index - start] = "§ROW§" + selected[line_index - start]
        else:
            selected[line_index - start] = re.sub(
                r"^(\s*)(\d{1,3})(\s{2,})", r"\1§ROW§\2\3", selected[line_index - start], count=1
            )
        value = clean(" ".join(selected))
        if 8 <= len(value) <= 8000:
            rows.append(value)
    return rows


def city_near_row_marker(raw: str, cities: list[dict[str, str]]) -> tuple[dict[str, str] | None, list[str]]:
    """Select the address nearest the numbered row anchor in layout text."""
    value = re.sub(r"\s+", "", raw)
    anchor = value.find("§ROW§")
    if anchor < 0:
        return None, []
    city_by_code = {city["city_code"]: city for city in cities}
    override_codes = [code for district, code in DISTRICT_CITY_OVERRIDES.items() if district in value]
    if len(set(override_codes)) == 1 and override_codes[0] in city_by_code:
        best = city_by_code[override_codes[0]]
        return {
            **best,
            "match_alias": next(district for district, code in DISTRICT_CITY_OVERRIDES.items() if code == override_codes[0] and district in value),
            "match_method": "official_district_to_prefecture_override",
        }, [override_codes[0]]
    distances: list[tuple[int, str, dict[str, str]]] = []
    for city in cities:
        token = re.sub(r"\s+", "", city.get("city_name", ""))
        start = 0
        while token:
            position = value.find(token, start)
            if position < 0:
                break
            distances.append((abs(position - anchor), city["city_code"], city))
            start = position + len(token)
    if not distances:
        return None, []
    distances.sort(key=lambda item: (item[0], item[1]))
    best_distance, best_code, best = distances[0]
    tied = sorted({code for distance, code, city in distances if distance == best_distance})
    if len(tied) > 1:
        return None, tied
    result = {**best, "match_alias": best.get("city_name", ""), "match_method": "nearest_full_city_name_to_pdf_row_anchor"}
    return result, [best_code]


def infer_entity(raw: str, cities: list[dict[str, str]]) -> str:
    value = re.sub(r"^.*?\b\d{1,3}\s+", "", raw, count=1)
    for city in cities:
        value = value.replace(city.get("city_name", ""), " ")
        value = value.replace(city.get("province_name", ""), " ")
    value = re.sub(r"第[一二三四五六七八九十0-9]+批", " ", value)
    value = re.sub(r"\s+", " ", value).strip(" |，,;")
    # This is a transparent parsing label, not a legal-entity identifier.
    return value[:120]


def save_download(project: Path, spec: dict[str, object], body: bytes, meta: dict[str, object]) -> Path:
    url_lower = str(spec["url"]).lower()
    suffix = ".html"
    for candidate in (".pdf", ".wps", ".docx", ".doc"):
        if candidate in url_lower or candidate in clean(spec.get("label", "")).lower():
            suffix = candidate
            break
    if "pdf" in str(meta.get("content_type", "")).lower():
        suffix = ".pdf"
    name = stable_id(spec["source_id"], spec["url"], length=12) + suffix
    path = project / RAW_DIR / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if body and (not path.is_file() or sha256_file(path) != str(meta.get("sha256", ""))):
        path.write_bytes(body)
    return path


def attachment_text(project: Path, path: Path) -> tuple[str, str]:
    if path.suffix.lower() == ".pdf":
        return pdf_text(path)
    if path.suffix.lower() not in {".wps", ".doc", ".docx"}:
        return "", "unsupported_attachment_type"
    cache = project / "05_intermediate" / "industrial_heritage_conversion_cache"
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / (path.stem + ".txt")
    completed = subprocess.run(
        ["libreoffice", "--headless", "--convert-to", "txt:Text", "--outdir", str(cache), str(path)],
        capture_output=True,
        timeout=180,
    )
    # LibreOffice writes <input_stem>.txt.  A previous deterministic conversion
    # is reusable if the command reports an existing output.
    if target.is_file() and target.stat().st_size:
        return target.read_text(encoding="utf-8", errors="replace"), "libreoffice_text_conversion"
    return "", f"libreoffice_conversion_failed:{completed.returncode}:{clean(completed.stderr)}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=PROJECT)
    args = parser.parse_args()
    project = args.project_root.resolve()
    cities = read_cities(project)
    evidence: list[dict[str, object]] = []
    attachments: list[dict[str, object]] = list(DIRECT_SPECS)

    for page in PAGE_SPECS:
        body, meta = fetch_url(str(page["url"]))
        path = save_download(project, page, body, meta)
        evidence.append({**page, **meta, "source_file": str(path.relative_to(project)), "evidence_type": "notice_page"})
        if int(meta.get("http_status", 0)) != 200:
            if page.get("required"):
                raise RuntimeError(f"Required MIIT notice unavailable: {page['url']} ({meta.get('error')})")
            continue
        for href, label in extract_links(body):
            url = urljoin(str(meta.get("final_url") or page["url"]), href)
            classification = classify_attachment(label, url, int(page["year"]))
            if not classification:
                continue
            attachments.append({
                "source_id": f"{page['source_id']}_{stable_id(url, length=8)}",
                "url": url,
                "label": label,
                "year": page["year"],
                "required": bool(page.get("required")),
                **classification,
            })

    # De-duplicate discovered/direct URLs, preferring explicit current specs.
    unique: dict[str, dict[str, object]] = {}
    for spec in attachments:
        unique.setdefault(str(spec["url"]).replace("wap.miit", "www.miit"), spec)
    attachments = list(unique.values())

    records: list[dict[str, object]] = []
    required_types: set[tuple[int, str, str]] = set()
    parsed_types: set[tuple[int, str, str]] = set()
    observed_years: set[int] = set()
    for spec in attachments:
        body, meta = fetch_url(str(spec["url"]))
        path = save_download(project, spec, body, meta)
        evidence.append({**spec, **meta, "source_file": str(path.relative_to(project)), "evidence_type": "list_attachment"})
        key = (int(spec["year"]), str(spec["event_type"]), str(spec["batch"]))
        if spec.get("required"):
            required_types.add(key)
        if int(meta.get("http_status", 0)) != 200 or len(body) < 1000:
            if spec.get("required"):
                raise RuntimeError(f"Required MIIT PDF unavailable: {spec['url']} ({meta.get('error')})")
            continue
        text, method = attachment_text(project, path)
        rows = context_rows(text)
        if not rows:
            if spec.get("required"):
                raise RuntimeError(f"No numbered records parsed from required MIIT PDF: {path}")
            continue
        parsed_types.add(key)
        if int(spec.get("formal", 0)):
            observed_years.add(int(spec["year"]))
        for sequence, raw in enumerate(rows, start=1):
            city, match_codes = city_near_row_marker(raw, cities)
            batch = str(spec["batch"])
            if "|" in batch:
                if "第一批" in raw:
                    batch = "1"
                elif "第二批" in raw:
                    batch = "2"
                elif "第三批" in raw:
                    batch = "3"
            flags = []
            if len(match_codes) > 1:
                flags.append("equidistant_multiple_297_city_mentions")
            if not match_codes:
                flags.append("no_exact_297_city_name")
            source_file = str(path.relative_to(project))
            row = {
                "record_id": stable_id(spec["source_id"], spec["year"], sequence, raw),
                "source_id": spec["source_id"],
                "event_year": spec["year"],
                "event_type": spec["event_type"],
                "batch": batch,
                "entity_name": infer_entity(raw, cities),
                "address_evidence": city.get("match_alias", "") if city else "",
                "raw_record": raw.replace("§ROW§", ""),
                "city_code": city.get("city_code", "") if city else "",
                "city_name": city.get("city_name", "") if city else "",
                "province_code": city.get("province_code", "") if city else "",
                "province_name": city.get("province_name", "") if city else "",
                "match_status": "matched_unique" if city else ("ambiguous" if match_codes else "unmatched"),
                "match_method": city.get("match_method", "") if city else "",
                "formal_flow_eligible": int(spec.get("formal", 0)) if city else 0,
                "source_url": spec["url"],
                "source_file": source_file,
                "source_sha256": meta.get("sha256", ""),
                "extraction_method": method + "+numbered_table_context",
                "qc_flags": "|".join(flags),
            }
            records.append(row)

    missing = sorted(required_types - parsed_types)
    if missing:
        raise RuntimeError(f"Required current industrial-heritage list types were not parsed: {missing}")
    # The 2025 notice must expose both seventh-batch and third-review files.
    current_2025 = {(year, kind, batch) for year, kind, batch in parsed_types if year == 2025}
    if not {(2025, "designation", "7"), (2025, "review_passed", "3")} <= current_2025:
        raise RuntimeError(f"2025 current notice attachments incomplete: {sorted(current_2025)}")

    write_csv(project / RECORDS, records, RECORD_FIELDS)
    write_csv(project / UNMATCHED, [row for row in records if not row["city_code"]], RECORD_FIELDS)
    evidence_fields = [
        "source_id", "evidence_type", "year", "event_type", "batch", "label",
        "requested_url", "final_url", "http_status", "content_type", "bytes",
        "sha256", "fetched_at_utc", "source_file", "required", "error",
    ]
    write_csv(project / SOURCES, evidence, evidence_fields)

    counts: Counter[tuple[str, int, str]] = Counter()
    for row in records:
        if row["formal_flow_eligible"]:
            counts[(str(row["city_code"]), int(row["event_year"]), str(row["event_type"]))] += 1
    panel = panel_base(cities)
    for row in panel:
        key = (str(row["city_code"]), int(row["year"]))
        observed = int(row["year"]) in observed_years
        row["industrial_heritage_list_observed"] = int(observed)
        for event_type, field in [
            ("designation", "industrial_heritage_designation_count"),
            ("review_passed", "industrial_heritage_review_passed_count"),
            ("review_rectification", "industrial_heritage_review_rectification_count"),
        ]:
            row[field] = counts[(key[0], key[1], event_type)] if observed else ""
    panel_fields = list(panel[0])
    write_csv(project / PANEL, panel, panel_fields)

    event_counts = Counter(str(row["event_type"]) for row in records)
    summary = {
        "status": "PASS",
        "task_id": "CR10",
        "current_required_complete": True,
        "generated_at_utc": utc_now(),
        "records": len(records),
        "matched_unique_records": sum(bool(row["city_code"]) for row in records),
        "unmatched_or_ambiguous_records": sum(not bool(row["city_code"]) for row in records),
        "formal_city_events": sum(int(row["formal_flow_eligible"]) for row in records),
        "event_type_counts": dict(sorted(event_counts.items())),
        "parsed_required_types": [list(item) for item in sorted(required_types)],
        "observed_formal_list_years": sorted(observed_years),
        "panel_rows": len(panel),
        "city_count": len({row["city_code"] for row in panel}),
        "old_notice_treatment": "first_second_third_old_notices_superseded; current review lists retained as lifecycle evidence",
        "batch5_treatment": "provisional public attachment excluded from formal designation flow unless a final official list is found",
        "batch8_2026_treatment": "application/review launch only; no final designation event counted",
    }
    write_json(project / SUMMARY, summary)
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
