#!/usr/bin/env python3
"""Build CR02/CR03/CR04/CR05/CR09 official-list city panels.

Every task writes an item-level record table, a retained unmatched table, a
297-city x 2012--2026 panel (4,455 rows), and a machine-readable QC summary.
Blank counts mean not observed/not safely zero; the worker never imputes zero.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import re
import urllib.parse
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable

from priority_official_entities_common import (
    YEARS,
    Geography,
    clean,
    download_official,
    extract_layout_text,
    extract_source_text,
    infer_year,
    load_download_events,
    load_geography,
    make_record,
    numbered_segments,
    official_attachment_urls,
    province_blocks,
    province_name_in,
    source_files,
    stable_id,
    write_csv,
    write_json,
)


TASK_SOURCES = {
    "CR02": {
        "cast_science_education_base_2022",
        "cast_science_education_base_supp_2022",
        "cast_science_demo_county_2023",
    },
    "CR03": {"miit_smart_elderly_2020", "miit_smart_elderly_2021", "miit_smart_elderly_2023"},
    "CR04": {"mct_cultural_park_2017", "mct_cultural_park_2020", "mct_cultural_industry_final_2024"},
    "CR05": {
        "cnipa_ip_strong_city_2022",
        "miit_advanced_manufacturing_cluster_2022",
        "miit_advanced_manufacturing_cluster_2024",
        "miit_sme_cluster_2022",
        "miit_sme_cluster_2023",
        "miit_sme_cluster_2024",
        "miit_industrial_internet_2023",
    },
    "CR09": {"mee_ecological_civilization_archive"},
}

DEFAULT_YEAR = {
    "cast_science_education_base_2022": 2022,
    "cast_science_education_base_supp_2022": 2022,
    "cast_science_demo_county_2023": 2023,
    "miit_smart_elderly_2020": 2020,
    "miit_smart_elderly_2021": 2021,
    "miit_smart_elderly_2023": 2023,
    "mct_cultural_park_2017": 2017,
    "mct_cultural_park_2020": 2020,
    "mct_cultural_industry_final_2024": 2024,
    "cnipa_ip_strong_city_2022": 2022,
    "miit_advanced_manufacturing_cluster_2022": 2022,
    "miit_advanced_manufacturing_cluster_2024": 2024,
    "miit_sme_cluster_2022": 2022,
    "miit_sme_cluster_2023": 2023,
    "miit_sme_cluster_2024": 2024,
    "miit_industrial_internet_2023": 2023,
    "miit_sme_cluster_2025_new": 2025,
    "miit_sme_cluster_2022_review_2025": 2025,
    "cnipa_ip_strong_city_creation_2026": 2026,
}

RECORD_FIELDS = [
    "record_id", "task", "source_id", "year", "measure", "list_status",
    "item_number", "province_raw", "location_raw", "entity_name", "item_text",
    "source_file", "source_url", "source_sha256", "extraction_method",
    "mapping_text", "city_code", "city_name", "province_code", "province_name",
    "match_status", "match_method", "match_evidence", "usable_for_panel",
    "qc_flags", "note",
]

MULTI_CLUSTER_CITIES = {
    "成德绵自凉": ("成都市", "德阳市", "绵阳市", "自贡市", "凉山彝族自治州"),
    "青烟威": ("青岛市", "烟台市", "威海市"),
    "沈大": ("沈阳市", "大连市"),
    "衡长株潭": ("衡阳市", "长沙市", "株洲市", "湘潭市"),
    "大盘": ("大连市", "盘锦市"),
    "金白兰武": ("金昌市", "白银市", "兰州市", "武威市"),
    "郑南商许": ("郑州市", "南阳市", "商丘市", "许昌市"),
    "鹰饶抚昌": ("鹰潭市", "上饶市", "抚州市", "南昌市"),
    "成渝": ("成都市", "重庆市"),
    "绥哈大齐": ("绥化市", "哈尔滨市", "大庆市", "齐齐哈尔市"),
    "乌昌石": ("乌鲁木齐市", "昌吉回族自治州", "石河子市"),
    "盐常宿淮": ("盐城市", "常州市", "宿迁市", "淮安市"),
    "广深佛惠莞中": ("广州市", "深圳市", "佛山市", "惠州市", "东莞市", "中山市"),
    # Hebei is not broadcast: the source abbreviation does not identify its
    # participating prefectures.  Beijing and Tianjin remain explicit.
    "京津冀": ("北京市", "天津市"),
}


def source_gap(
    task: str,
    source: dict[str, str],
    geography: Geography,
    reason: str,
    year: int | None = None,
) -> dict[str, Any]:
    year = year if year is not None else DEFAULT_YEAR.get(source.get("source_id", ""), 2026)
    return make_record(
        task=task,
        source=source,
        year=year,
        measure="source_attachment_gap",
        list_status="source_gap",
        item_number="",
        province_raw="",
        location_raw="",
        entity_name="[OFFICIAL LIST ATTACHMENT NOT AVAILABLE IN RAW CACHE]",
        item_text=reason,
        extraction_method="official_notice_evidence_only",
        geography=geography,
        note=reason,
    )


def fetch_declared_attachments(
    project_root: Path,
    sources: list[dict[str, str]],
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Fetch only attachments explicitly declared by cached official pages."""
    downloaded = []
    audit = []
    seen = set()
    for source in sources:
        for url in official_attachment_urls(source):
            if url in seen:
                continue
            seen.add(url)
            row = download_official(project_root, source["source_id"], url)
            row["discovered_from"] = source["source_url"]
            audit.append(row)
            if not row.get("error"):
                downloaded.append(row)
    return downloaded, audit


def pipe_parts(segment: str) -> list[str]:
    return [clean(cell) for cell in segment.split("|") if clean(cell)]


def build_cr02(sources: list[dict[str, str]], geography: Geography) -> list[dict[str, Any]]:
    records = []
    for source in sources:
        text, method = extract_source_text(source)
        sid = source["source_id"]
        if Path(source["path"]).suffix.lower() == ".html":
            continue
        if sid == "cast_science_demo_county_2023":
            marker = text.find("名单")
            for province, block in province_blocks(text, geography, max(marker, 0)):
                block = re.sub(r"^[\uff08(]\d+[\uff09)]\s*\|?", "", block)
                for index, item in enumerate(re.split(r"[、；;]", block), 1):
                    item = clean(item)
                    if not item or "科普示范" in item:
                        continue
                    records.append(make_record(
                        task="CR02", source=source, year=2023,
                        measure="science_demonstration_county",
                        list_status="designation", item_number=index,
                        province_raw=province, location_raw=item, entity_name=item,
                        item_text=item, extraction_method=method + "+province_block_item",
                        geography=geography,
                    ))
            continue
        measure = "science_education_base_supplement" if "supp" in sid else "science_education_base"
        for _, number, segment in numbered_segments(text):
            cells = pipe_parts(segment)
            if not cells:
                continue
            province = cells[0]
            entity = cells[2] if len(cells) >= 3 else cells[-1]
            records.append(make_record(
                task="CR02", source=source, year=2022, measure=measure,
                list_status="designation", item_number=number,
                province_raw=province, location_raw=province, entity_name=entity,
                item_text=segment, extraction_method=method + "+numbered_table",
                geography=geography,
            ))
    return records


def category_numbered_segments(text: str) -> list[tuple[int, int, str]]:
    segments = numbered_segments(text)
    if segments:
        return segments
    match = re.search(r"(?<!\d)1\s+(?=[\u4e00-\u9fff])(.+)", clean(text))
    return [(match.start(), 1, clean(match.group(1)))] if match else []


def smart_section_blocks(text: str) -> list[tuple[str, str]]:
    """Split lists whose numbering restarts at each official category."""
    labels = (
        ("示范企业", "smart_elderly_enterprise"),
        ("示范园区", "smart_elderly_park"),
        ("示范街道", "smart_elderly_street_town"),
        ("示范基地", "smart_elderly_base"),
    )
    positions = []
    for label, measure in labels:
        positions.extend((match.start(), match.end(), measure) for match in re.finditer(label, text))
    positions.sort()
    output = []
    for index, (_, begin, measure) in enumerate(positions):
        end = positions[index + 1][0] if index + 1 < len(positions) else len(text)
        block = text[begin:end]
        if category_numbered_segments(block):
            output.append((measure, block))
    if output:
        return output
    return [("smart_elderly_item", text)]


def build_cr03(
    project_root: Path,
    sources: list[dict[str, str]],
    geography: Geography,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    records = []
    attachments, fetch_log = fetch_declared_attachments(project_root, sources)
    sources = sorted(sources + attachments, key=lambda row: (row["source_id"], row["source_file"]))
    attachment_by_source = Counter(
        row["source_id"] for row in sources if Path(row["path"]).suffix.lower() != ".html"
    )
    for source in sources:
        text, method = extract_source_text(source)
        sid = source["source_id"]
        if Path(source["path"]).suffix.lower() == ".html":
            if not attachment_by_source[sid]:
                records.append(source_gap("CR03", source, geography, "Notice names attachments, but no attachment snapshot is cached."))
            continue
        layout, layout_method = extract_layout_text(source, text)
        normalized = clean(layout)
        sections = smart_section_blocks(normalized)
        segment_count = sum(len(category_numbered_segments(block)) for _, block in sections)
        if not segment_count:
            records.append(source_gap("CR03", source, geography, "Downloaded attachment could not be segmented into numbered records."))
            continue
        for measure, block in sections:
            for _, number, segment in category_numbered_segments(block):
                province = province_name_in(segment[:120], geography)
                year_match = re.search(r"(?<!\d)(201[7-9]|202[0-3])(?!\d)", segment)
                year = int(year_match.group(1)) if year_match and "复核" in normalized[:300] else DEFAULT_YEAR[sid]
                records.append(make_record(
                    task="CR03", source=source, year=year, measure=measure,
                    list_status="review_pass" if "复核名单" in normalized[:300] else "designation",
                    item_number=number, province_raw=province,
                    location_raw=segment[:200], entity_name=segment,
                    item_text=segment, extraction_method=layout_method + "+category_numbered_table",
                    geography=geography,
                ))
    return records, fetch_log


def cultural_pdf_lines(text: str, geography: Geography) -> list[tuple[str, str]]:
    province_names = {row["province_name"] for row in geography.cities}
    output = []
    current = ""
    for raw in text.splitlines():
        line = clean(raw)
        if not line or re.fullmatch(r"\d+", line):
            continue
        if line in province_names:
            current = line
            continue
        if line == "中央企业":
            current = "[CENTRAL_ENTERPRISE]"
            continue
        if not current:
            continue
        if any(token in line for token in ("附件", "名单", "按行政区划排序")):
            continue
        output.append((current, line))
    return output


def build_cr04(sources: list[dict[str, str]], geography: Geography) -> list[dict[str, Any]]:
    records = []
    for source in sources:
        text, method = extract_source_text(source)
        sid = source["source_id"]
        year = DEFAULT_YEAR[sid]
        if sid in {"mct_cultural_park_2017", "mct_cultural_park_2020"}:
            starts = [position for marker in ("名单如下", "名单公布如下") if (position := text.find(marker)) >= 0]
            start = min(starts) if starts else 0
            end_candidates = [p for p in (text.find("请上述", start), text.find("希望", start)) if p > start]
            body = text[start : min(end_candidates) if end_candidates else len(text)]
            for number, (province, block) in enumerate(province_blocks(body, geography), 1):
                entity = re.sub(r"^[\uff08(]\d+[\uff09)]", "", clean(block)).strip(" |、")
                if not entity:
                    continue
                status = "creation_qualification" if year == 2017 else "creation_list"
                records.append(make_record(
                    task="CR04", source=source, year=year,
                    measure="national_cultural_industry_park",
                    list_status=status, item_number=number,
                    province_raw="" if province == "[CENTRAL_ENTERPRISE]" else province,
                    location_raw=entity, entity_name=entity,
                    item_text=entity, extraction_method=method + "+province_heading_list",
                    geography=geography,
                ))
            continue
        if Path(source["path"]).suffix.lower() != ".pdf":
            continue
        layout, layout_method = extract_layout_text(source, text)
        status = "review_pass" if "通过复核" in clean(layout[:500]) else "designation"
        measure = "national_cultural_industry_base_review_pass" if status == "review_pass" else "national_cultural_industry_base_new"
        for number, (province, entity) in enumerate(cultural_pdf_lines(layout, geography), 1):
            records.append(make_record(
                task="CR04", source=source, year=2024, measure=measure,
                list_status=status, item_number=number,
                province_raw="" if province == "[CENTRAL_ENTERPRISE]" else province,
                location_raw=entity, entity_name=entity,
                item_text=entity, extraction_method=layout_method + "+province_heading_lines",
                geography=geography,
            ))
    return records


def multi_cluster_records(
    source: dict[str, str], year: int, number: int, entity: str, geography: Geography
) -> list[dict[str, Any]]:
    declared: tuple[str, ...] = ()
    evidence_token = ""
    for token, names in MULTI_CLUSTER_CITIES.items():
        if token not in entity:
            continue
        declared = names
        evidence_token = token
        break
    if not declared:
        explicit = tuple(row["city_name"] for row in geography.cities if row["city_name"] in entity)
        if len(explicit) > 1:
            declared = explicit
            evidence_token = "explicit_official_city_names"
    if declared:
        output = []
        for name in declared:
            city = next((row for row in geography.cities if row["city_name"] == name), None)
            if not city:
                continue
            record = make_record(
                task="CR05", source=source, year=year,
                measure="advanced_manufacturing_cluster",
                list_status="designation", item_number=number,
                province_raw=city["province_name"], location_raw=name,
                entity_name=entity, item_text=entity,
                extraction_method="shared_extract_text+pypdf_layout+declared_multi_city_crosswalk",
                geography=geography,
                note=f"Multi-city cluster; conservative crosswalk evidence={evidence_token}.",
            )
            record["qc_flags"] = "|".join(filter(None, (record["qc_flags"], "multi_city_cluster")))
            output.append(record)
        if output:
            return output
    return []


def all_numbered_sequences(text: str) -> list[tuple[int, int, str]]:
    """Parse multiple official sublists whose numbering restarts at one."""
    normalized = clean(text)
    pattern = re.compile(
        r"(?<![\dA-Za-z])([1-9]\d{0,3})\s*(?:[.\uff0e\u3001]\s*|\|\s*|\s+)(?=[\u4e00-\u9fffA-Za-z\"\u201c(\uff08])"
    )
    candidates = []
    for match in pattern.finditer(normalized):
        following = clean(normalized[match.end() : match.end() + 30])
        if following.startswith(("序号", "附件")):
            continue
        candidates.append(match)
    sequences: list[list[re.Match[str]]] = []
    active: list[re.Match[str]] = []
    for match in candidates:
        number = int(match.group(1))
        if number == 1:
            if active:
                sequences.append(active)
            active = [match]
        elif active and number == int(active[-1].group(1)) + 1:
            active.append(match)
    if active:
        sequences.append(active)
    accepted = [(sequence_id, match) for sequence_id, sequence in enumerate(sequences, 1) for match in sequence]
    output = []
    for index, (sequence_id, match) in enumerate(accepted):
        end = accepted[index + 1][1].start() if index + 1 < len(accepted) else len(normalized)
        segment = clean(normalized[match.end() : end]).strip(" |；;")
        if segment:
            output.append((sequence_id, int(match.group(1)), segment))
    return output


def location_numbered_segments(text: str, geography: Geography) -> list[tuple[int, int, str]]:
    """Parse numbered location lists even when PDF extraction joins ``10河北``."""
    normalized = clean(text)
    locations = sorted(
        {row["province_name"] for row in geography.cities}
        | {row["city_name"] for row in geography.cities}
        | {"新疆生产建设兵团"},
        key=len,
        reverse=True,
    )
    pattern = re.compile(r"(?<!\d)([1-9]\d{0,3})\s*(?=(?:" + "|".join(map(re.escape, locations)) + r"))")
    matches = list(pattern.finditer(normalized))
    if not matches:
        return []
    by_number = {}
    for match in matches:
        number = int(match.group(1))
        by_number.setdefault(number, match)
    maximum = max(by_number)
    # A rare wrapped PDF row may put the item number after its location.  Keep
    # that row as unmatched evidence rather than dropping it or borrowing a
    # neighbouring city's location.
    for number in range(1, maximum + 1):
        if number in by_number:
            continue
        previous = by_number.get(number - 1)
        following = by_number.get(number + 1)
        if not previous or not following:
            continue
        candidates = list(re.finditer(rf"(?<!\d){number}\s+(?=[\u4e00-\u9fff])", normalized[previous.end() : following.start()]))
        candidates = [candidate for candidate in candidates if not normalized[previous.end() + candidate.end() :].startswith("序号")]
        if len(candidates) == 1:
            candidate = candidates[0]
            by_number[number] = re.compile(rf"(?<!\d)({number})\s+").search(
                normalized,
                previous.end() + candidate.start(),
                previous.end() + candidate.end(),
            )
    sequence = [by_number[number] for number in sorted(by_number) if by_number[number] is not None]
    if len(sequence) < 3:
        return []
    return [
        (
            match.start(),
            int(match.group(1)),
            clean(normalized[match.end() : sequence[index + 1].start() if index + 1 < len(sequence) else len(normalized)]).strip(" |；;"),
        )
        for index, match in enumerate(sequence)
    ]


def fetch_cr05_supplements(project_root: Path) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    specifications = [
        (
            "miit_sme_cluster_2025_new",
            "https://www.miit.gov.cn/jgsj/qyj/wjfb/art/2025/art_b5e99d115a5345e1be8fb42add097be7.html",
        ),
        (
            "miit_sme_cluster_2025_new",
            "https://www.miit.gov.cn/cms_files/filemanager/1226211233/attach/202510/b38d359736bc48408f7977561e84bd02.pdf",
        ),
        (
            "miit_sme_cluster_2022_review_2025",
            "https://www.miit.gov.cn/cms_files/filemanager/1226211233/attach/202510/f9f5b17a312242b489be012fb37359c5.pdf",
        ),
        (
            "cnipa_ip_strong_city_creation_2026",
            "https://www.cnipa.gov.cn/art/2026/3/2/art_75_204817.html",
        ),
    ]
    downloaded = [download_official(project_root, source_id, url) for source_id, url in specifications]
    return [row for row in downloaded if not row.get("error")], downloaded


def cnipa_creation_records(source: dict[str, str], text: str, geography: Geography) -> list[dict[str, Any]]:
    match = re.search(
        r"一、国家知识产权强市建设示范市创建对象\s*(.*?)\s*二、",
        text,
    )
    if not match:
        return []
    output = []
    for number, item in enumerate(re.split(r"[、,，]", match.group(1)), 1):
        item = clean(item)
        if not item:
            continue
        output.append(make_record(
            task="CR05", source=source, year=2026,
            measure="ip_strong_city_creation_object",
            list_status="creation_candidate_pending_2027_evaluation",
            item_number=number, province_raw="", location_raw=item,
            entity_name=item, item_text=item,
            extraction_method="shared_extract_text+explicit_inline_section",
            geography=geography,
            note="Creation object only; must not be counted as a formally recognized demonstration city.",
        ))
    return output


def build_cr05(
    project_root: Path,
    sources: list[dict[str, str]],
    geography: Geography,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    supplemental, fetch_log = fetch_cr05_supplements(project_root)
    declared, declared_log = fetch_declared_attachments(project_root, sources)
    fetch_log.extend(declared_log)
    by_file = {row["source_file"]: row for row in sources}
    by_file.update({row["source_file"]: row for row in declared})
    by_file.update({row["source_file"]: row for row in supplemental})
    sources = sorted(by_file.values(), key=lambda row: (row["source_id"], row["source_file"]))
    records = []
    attachment_by_source = Counter(
        row["source_id"] for row in sources if Path(row["path"]).suffix.lower() != ".html"
    )
    for source in sources:
        text, method = extract_source_text(source)
        sid = source["source_id"]
        suffix = Path(source["path"]).suffix.lower()
        if sid == "cnipa_ip_strong_city_creation_2026":
            records.extend(cnipa_creation_records(source, text, geography))
            continue
        if suffix == ".html":
            if sid in {"cnipa_ip_strong_city_2022", "miit_advanced_manufacturing_cluster_2022", "miit_industrial_internet_2023"} and not attachment_by_source[sid]:
                records.append(source_gap("CR05", source, geography, "Official notice references a list attachment that is absent from the raw cache."))
            continue
        layout, layout_method = extract_layout_text(source, text)
        segments = (
            location_numbered_segments(layout, geography)
            if sid.startswith("miit_sme_cluster_")
            else all_numbered_sequences(layout)
            if sid == "miit_industrial_internet_2023"
            else numbered_segments(clean(layout))
        )
        if not segments:
            records.append(source_gap("CR05", source, geography, "Official attachment could not be segmented into numbered records."))
            continue
        current_province = ""
        for group, number, segment in segments:
            entity = clean(segment.split("|", 1)[-1])
            if sid == "cnipa_ip_strong_city_2022":
                current_province = province_name_in(segment[:80], geography) or current_province
                demonstration = "示范城市名单" in clean(layout[:200])
                records.append(make_record(
                    task="CR05", source=source, year=2022,
                    measure="ip_strong_city_demonstration" if demonstration else "ip_strong_city_pilot",
                    list_status="demonstration_designation" if demonstration else "pilot_designation",
                    item_number=number, province_raw=current_province,
                    location_raw=segment, entity_name=entity, item_text=segment,
                    extraction_method=layout_method + "+numbered_list_with_province_carry_forward",
                    geography=geography,
                ))
                continue
            if sid in {"miit_advanced_manufacturing_cluster_2022", "miit_advanced_manufacturing_cluster_2024"}:
                year = DEFAULT_YEAR[sid]
                multi = multi_cluster_records(source, year, number, entity, geography)
                if multi:
                    records.extend(multi)
                    continue
                records.append(make_record(
                    task="CR05", source=source, year=year,
                    measure="advanced_manufacturing_cluster", list_status="designation",
                    item_number=number, province_raw="", location_raw=entity,
                    entity_name=entity, item_text=entity,
                    extraction_method=layout_method + "+numbered_list",
                    geography=geography,
                ))
                continue
            if sid == "miit_industrial_internet_2023":
                records.append(make_record(
                    task="CR05", source=source, year=2023,
                    measure="industrial_internet_pilot_project",
                    list_status="pilot_demonstration", item_number=f"{group}:{number}",
                    province_raw=province_name_in(segment, geography),
                    location_raw=segment, entity_name=entity, item_text=segment,
                    extraction_method=layout_method + "+restarting_numbered_sublists",
                    geography=geography,
                ))
                continue
            if sid.startswith("miit_sme_cluster_"):
                review = sid == "miit_sme_cluster_2022_review_2025" or "复核" in clean(layout[:500])
                year = DEFAULT_YEAR[sid]
                records.append(make_record(
                    task="CR05", source=source, year=year,
                    measure="sme_cluster_review_pass" if review else "sme_cluster_designation",
                    list_status="review_pass" if review else "designation",
                    item_number=number, province_raw=province_name_in(entity, geography),
                    location_raw=entity, entity_name=entity, item_text=entity,
                    extraction_method=layout_method + "+numbered_list",
                    geography=geography,
                    note="2025 review-pass events are lifecycle events, not new designations." if review else "",
                ))
    return records, fetch_log


ECO_TOPIC = re.compile(r"生态文明建设示范区|绿水青山就是金山银山|实践创新基地")
ECO_ACTION = re.compile(r"命名|创建|名单|示范建设")


def eco_mentions(text: str, geography: Geography) -> dict[str, tuple[str, str, str]]:
    output: dict[str, tuple[str, str, str]] = {}
    for city in geography.cities:
        name = city["city_name"]
        for match in re.finditer(re.escape(name), text):
            snippet = clean(text[max(0, match.start() - 160) : match.end() + 160])
            if ECO_TOPIC.search(snippet) or ECO_ACTION.search(snippet):
                output[city["city_code"]] = (name, "official_prefecture_name", snippet)
                break
    for county_name, parent_code, _ in geography.matcher.counties:
        if parent_code not in geography.city_by_code or len(county_name) < 3:
            continue
        for match in re.finditer(re.escape(county_name), text):
            snippet = clean(text[max(0, match.start() - 160) : match.end() + 160])
            if ECO_TOPIC.search(snippet) or ECO_ACTION.search(snippet):
                output.setdefault(parent_code, (county_name, "official_county_code_parent", snippet))
                break
    return output


def build_cr09(sources: list[dict[str, str]], geography: Geography) -> list[dict[str, Any]]:
    records = []
    for source in sources:
        try:
            text, method = extract_source_text(source)
        except Exception as exc:
            records.append(source_gap(
                "CR09", source, geography,
                f"Text extraction failed: {type(exc).__name__}: {exc}",
                infer_year(source, "", 2026),
            ))
            continue
        if not ECO_TOPIC.search(text) or not ECO_ACTION.search(text):
            continue
        year = infer_year(source, text, 2026)
        mentions = eco_mentions(text, geography)
        if not mentions:
            records.append(source_gap(
                "CR09", source, geography,
                "Ecological-civilization item has no conservatively mappable 297-city location.",
                year,
            ))
            continue
        for number, (city_code, (evidence, mention_kind, snippet)) in enumerate(sorted(mentions.items()), 1):
            city = geography.city_by_code[city_code]
            record = make_record(
                task="CR09", source=source, year=year,
                measure="ecological_civilization_official_document_incidence",
                list_status="official_archive_document", item_number=number,
                province_raw=city["province_name"], location_raw=city["city_name"],
                entity_name=evidence, item_text=snippet,
                extraction_method=method + "+contextual_official_division_mention",
                geography=geography,
                note="Document-city incidence; not assumed to be a new designation count.",
            )
            record["match_method"] = mention_kind
            record["match_evidence"] = evidence
            if mention_kind == "official_county_code_parent":
                record["qc_flags"] = "|".join(filter(None, (record["qc_flags"], "county_to_prefecture_rollup")))
            records.append(record)
    return records


def deduplicate(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = {}
    for row in records:
        key = (
            row["source_sha256"], row["year"], row["measure"], row["list_status"],
            row["item_number"], row["entity_name"], row["city_code"], row["match_status"],
        )
        output.setdefault(key, row)
    return sorted(output.values(), key=lambda row: (int(row["year"]), row["source_id"], str(row["item_number"]), row["record_id"]))


def build_panel(task: str, records: list[dict[str, Any]], geography: Geography) -> tuple[list[dict[str, Any]], list[str]]:
    measures = sorted({row["measure"] for row in records if row["measure"] != "source_attachment_gap"})
    counts: Counter[tuple[str, int, str]] = Counter()
    observed = defaultdict(set)
    for row in records:
        if row["measure"] == "source_attachment_gap":
            continue
        observed[(int(row["year"]), row["measure"])].add(row["source_id"])
        if row["usable_for_panel"] == "1":
            counts[(row["city_code"], int(row["year"]), row["measure"])] += 1
    rows = []
    for city in geography.cities:
        for year in YEARS:
            row: dict[str, Any] = {
                "city_code": city["city_code"], "city_name": city["city_name"],
                "province_code": city["province_code"], "province_name": city["province_name"],
                "year": year, "task": task,
            }
            for measure in measures:
                value = counts.get((city["city_code"], year, measure))
                row[f"{measure}_count"] = value if value else ""
                row[f"{measure}_source_year_observed"] = 1 if observed.get((year, measure)) else 0
                row[f"{measure}_safe_zero"] = 0
            rows.append(row)
    return rows, measures


def output_paths(project_root: Path, task: str) -> dict[str, Path]:
    lower = task.lower()
    return {
        "records": project_root / "05_intermediate" / f"priority_{lower}_records.csv",
        "unmatched": project_root / "10_qc" / f"priority_{lower}_unmatched.csv",
        "panel": project_root / "06_panel" / f"priority_{lower}_297_city_year_2012_2026.csv",
        "summary": project_root / "10_qc" / f"priority_{lower}_summary.json",
        "supplemental": project_root / "05_intermediate" / f"priority_{lower}_supplemental_sources.csv",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--task", choices=sorted(TASK_SOURCES), required=True)
    args = parser.parse_args()
    project_root = args.project_root.resolve()
    geography = load_geography(project_root)
    events = load_download_events(project_root)
    sources = source_files(project_root, TASK_SOURCES[args.task], events)
    fetch_log: list[dict[str, str]] = []
    if args.task == "CR02":
        records = build_cr02(sources, geography)
    elif args.task == "CR03":
        records, fetch_log = build_cr03(project_root, sources, geography)
    elif args.task == "CR04":
        records = build_cr04(sources, geography)
    elif args.task == "CR05":
        records, fetch_log = build_cr05(project_root, sources, geography)
    else:
        records = build_cr09(sources, geography)
    records = deduplicate(records)
    unmatched = [row for row in records if row["usable_for_panel"] != "1"]
    panel, measures = build_panel(args.task, records, geography)
    paths = output_paths(project_root, args.task)
    write_csv(paths["records"], records, RECORD_FIELDS)
    write_csv(paths["unmatched"], unmatched, RECORD_FIELDS)
    panel_fields = list(panel[0]) if panel else []
    write_csv(paths["panel"], panel, panel_fields)
    if fetch_log:
        fields = sorted({key for row in fetch_log for key in row})
        write_csv(paths["supplemental"], fetch_log, fields)

    usable = [row for row in records if row["usable_for_panel"] == "1"]
    checks = {
        "city_master_is_297": len(geography.cities) == 297,
        "panel_has_4455_rows": len(panel) == 4455,
        "panel_key_unique": len({(row["city_code"], row["year"]) for row in panel}) == 4455,
        "all_usable_codes_in_city_master": all(row["city_code"] in geography.city_by_code for row in usable),
        "all_unusable_records_retained": len(unmatched) == sum(row["usable_for_panel"] != "1" for row in records),
        "no_silent_zero_fill": all(
            value != 0
            for row in panel
            for key, value in row.items()
            if key.endswith("_count")
        ),
        "record_ids_unique": len({row["record_id"] for row in records}) == len(records),
    }
    structural_pass = all(checks.values())
    summary = {
        "status": "PASS" if structural_pass else "FAIL",
        "task": args.task,
        "checks": checks,
        "mca_snapshot_sha256": geography.mca_sha256,
        "source_files": len(sources),
        "records": len(records),
        "usable_records": len(usable),
        "unmatched_records": len(unmatched),
        "panel_rows": len(panel),
        "measures": measures,
        "records_by_measure": dict(sorted(Counter(row["measure"] for row in records).items())),
        "records_by_year": dict(sorted(Counter(str(row["year"]) for row in records).items())),
        "records_by_match_status": dict(sorted(Counter(row["match_status"] for row in records).items())),
        "unique_official_items_by_source": {
            source_id: len({
                (row["source_sha256"], row["measure"], str(row["item_number"]))
                for row in records
                if row["source_id"] == source_id and row["measure"] != "source_attachment_gap"
            })
            for source_id in sorted({row["source_id"] for row in records})
        },
        "records_outside_panel_years": sum(int(row["year"]) not in YEARS for row in records),
        "source_gaps": [
            {"source_id": row["source_id"], "source_url": row["source_url"], "reason": row["note"]}
            for row in records if row["list_status"] == "source_gap"
        ],
        "supplemental_fetch": fetch_log,
        "official_fetch_failures": [row for row in fetch_log if row.get("error")],
        "zero_policy": "Count cells are positive-only. Blank means absent or not safely zero; *_safe_zero is always 0.",
        "outputs": {name: path.relative_to(project_root).as_posix() for name, path in paths.items() if path.exists()},
    }
    write_json(paths["summary"], summary)
    if not structural_pass:
        failed = [name for name, passed in checks.items() if not passed]
        raise AssertionError(f"{args.task} structural QC failed: {failed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
