#!/usr/bin/env python3
"""Shared, conservative helpers for priority official-list workers.

The helpers intentionally depend on the project's existing MCA parser and
``DivisionMatcher``.  They never invent a prefecture from a province-only
location and restrict usable matches to the frozen 297-city research frame.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import html
import json
import re
import sys
import tempfile
import types
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

# ``build_official_lists_panel`` imports pdfplumber for its own PDF-table
# extractors.  This worker only reuses its MCA parser/matcher.  Permit a local
# validation environment without pdfplumber while leaving a real installation
# untouched on the cloud worker.
try:  # pragma: no cover - depends on runtime package set
    import pdfplumber as _pdfplumber  # noqa: F401
except ImportError:  # pragma: no cover
    sys.modules["pdfplumber"] = types.ModuleType("pdfplumber")

from build_full_collection_text_index import extract_text
from build_official_lists_panel import (
    DivisionMatcher,
    build_prefecture_universe,
    parse_mca_divisions,
)


YEARS = tuple(range(2012, 2027))


def clean(value: Any) -> str:
    return re.sub(r"\s+", " ", html.unescape(str(value or ""))).strip()


def stable_id(*parts: Any) -> str:
    raw = "\x1f".join(clean(part) for part in parts).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:24]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: Iterable[dict[str, Any]], fields: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


@dataclass(frozen=True)
class Geography:
    cities: tuple[dict[str, str], ...]
    city_by_code: dict[str, dict[str, str]]
    matcher: DivisionMatcher
    divisions: tuple[dict[str, str], ...]
    mca_sha256: str


def load_geography(project_root: Path) -> Geography:
    cities = tuple(read_csv(project_root / "04_crosswalk" / "city_master_297_snapshot.csv"))
    if len(cities) != 297 or len({row["city_code"] for row in cities}) != 297:
        raise ValueError("Frozen city master must contain 297 unique city codes.")
    snapshot = project_root / "05_intermediate" / "official_lists_mca_map_snapshot.html"
    payload = snapshot.read_bytes()
    divisions = tuple(parse_mca_divisions(payload.decode("gb18030", errors="replace")))
    universe = build_prefecture_universe(list(divisions))
    return Geography(
        cities=cities,
        city_by_code={row["city_code"]: row for row in cities},
        matcher=DivisionMatcher(list(divisions), universe),
        divisions=divisions,
        mca_sha256=hashlib.sha256(payload).hexdigest(),
    )


def load_download_events(project_root: Path) -> list[dict[str, str]]:
    return read_csv(project_root / "01_source_register" / "download_events.csv")


def source_files(
    project_root: Path,
    source_ids: set[str],
    events: list[dict[str, str]],
) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    seen: set[str] = set()
    for event in events:
        if event.get("source_id") not in source_ids or event.get("error"):
            continue
        rel = event.get("saved_path", "")
        if not rel or rel in seen:
            continue
        path = project_root / rel
        if not path.is_file():
            continue
        seen.add(rel)
        output.append(
            {
                "source_id": event["source_id"],
                "path": str(path),
                "source_file": rel,
                "source_url": event.get("url", ""),
                "parent_url": event.get("parent_url", ""),
                "content_type": event.get("content_type", ""),
                "source_sha256": event.get("sha256", "") or sha256_file(path),
                "fetched_at_utc": event.get("fetched_at_utc", ""),
            }
        )
    return sorted(output, key=lambda row: (row["source_id"], row["source_file"]))


def extract_source_text(source: dict[str, str]) -> tuple[str, str]:
    """Use the shared text extractor, with a transparent gzip transport fix."""
    path = Path(source["path"])
    payload = path.read_bytes()[:2]
    if payload != b"\x1f\x8b":
        return extract_text(path, source.get("content_type", ""))
    with tempfile.NamedTemporaryFile(suffix=path.suffix) as handle:
        handle.write(gzip.decompress(path.read_bytes()))
        handle.flush()
        text, method = extract_text(Path(handle.name), source.get("content_type", "text/html"))
    return text, "gzip_transport+" + method


def extract_layout_text(source: dict[str, str], fallback: str) -> tuple[str, str]:
    """Preserve PDF line boundaries when pypdf supports layout extraction."""
    path = Path(source["path"])
    if path.suffix.lower() != ".pdf":
        return fallback, "shared_extract_text"
    try:
        from pypdf import PdfReader

        parts = []
        for page in PdfReader(str(path)).pages:
            try:
                parts.append(page.extract_text(extraction_mode="layout") or "")
            except TypeError:
                parts.append(page.extract_text() or "")
        return "\n".join(parts), "shared_extract_text+pypdf_layout"
    except Exception:
        return fallback, "shared_extract_text"


def infer_year(source: dict[str, str], text: str, default: int) -> int:
    for candidate in (source.get("source_url", ""), source.get("parent_url", "")):
        match = re.search(r"(?:/|t)(20(?:1\d|2\d))(?:\d{2})?", candidate)
        if match:
            return int(match.group(1))
    years = [int(value) for value in re.findall(r"(?<!\d)(20(?:1\d|2[0-6]))(?!\d)", text[:1000])]
    return years[0] if years else default


def province_name_in(text: str, geography: Geography) -> str:
    code, name = geography.matcher.find_province(text)
    return name if code else ""


def numbered_segments(text: str) -> list[tuple[int, int, str]]:
    """Return the longest plausible 1..N list while retaining source offsets."""
    normalized = clean(text)
    pattern = re.compile(
        r"(?<![\dA-Za-z])([1-9]\d{0,3})\s*(?:[.\uff0e\u3001]\s*|\|\s*|\s+)(?=[\u4e00-\u9fffA-Za-z\"\u201c(\uff08])"
    )
    matches = list(pattern.finditer(normalized))
    best: list[re.Match[str]] = []
    for start_index, start in enumerate(matches):
        if int(start.group(1)) != 1:
            continue
        sequence = [start]
        expected = 2
        for candidate in matches[start_index + 1 :]:
            distance = candidate.start() - sequence[-1].start()
            if distance > 2500:
                break
            number = int(candidate.group(1))
            if number == expected:
                sequence.append(candidate)
                expected += 1
        if len(sequence) > len(best):
            best = sequence
    if len(best) < 3:
        return []
    output = []
    for index, match in enumerate(best):
        end = best[index + 1].start() if index + 1 < len(best) else min(len(normalized), match.end() + 1500)
        segment = clean(normalized[match.end() : end]).strip(" |;\uff1b")
        if segment:
            output.append((match.start(), int(match.group(1)), segment))
    return output


def province_blocks(text: str, geography: Geography, start_at: int = 0) -> list[tuple[str, str]]:
    """Split an official list at explicit province headings, without inference."""
    province_names = sorted(
        {row["province_name"] for row in geography.cities}, key=len, reverse=True
    )
    aliases: list[tuple[str, str]] = []
    for name in province_names:
        aliases.append((name, name))
        for suffix in ("壮族自治区", "维吾尔自治区", "回族自治区", "自治区", "省", "市"):
            if name.endswith(suffix) and len(name) > len(suffix) + 1:
                aliases.append((name[: -len(suffix)], name))
    positions = []
    for alias, official in aliases:
        for match in re.finditer(re.escape(alias), text[start_at:]):
            position = start_at + match.start()
            before = text[position - 1 : position] if position else " "
            after = text[position + len(alias) : position + len(alias) + 1]
            if (not before or before.isspace() or before in "|;\uff1b") and (
                not after or after.isspace() or after in "(\uff08|"
            ):
                positions.append((position, position + len(alias), official))
    positions.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    deduped = []
    occupied = -1
    for item in positions:
        if item[0] < occupied:
            continue
        deduped.append(item)
        occupied = item[1]
    output = []
    for index, (begin, content_start, official) in enumerate(deduped):
        end = deduped[index + 1][0] if index + 1 < len(deduped) else len(text)
        block = clean(text[content_start:end]).strip(" |;\uff1b")
        if block:
            output.append((official, block))
    return output


def download_official(
    project_root: Path,
    source_id: str,
    url: str,
    timeout: int = 90,
) -> dict[str, str]:
    """Download an official URL into content-addressed raw storage."""
    parsed_requested = urllib.parse.urlparse(url)
    requested_name = Path(parsed_requested.path).name
    query = urllib.parse.parse_qs(parsed_requested.query)
    for key in ("fileUrl", "filename", "fileName"):
        if query.get(key):
            requested_name = Path(query[key][0]).name
            break
    requested_stem = re.sub(
        r"[^A-Za-z0-9._-]", "_", Path(requested_name).stem
    )[:80] or "official"
    cache_dir = project_root / "03_external_raw" / source_id
    cached = sorted(cache_dir.glob(f"*__{requested_stem}.*")) if cache_dir.exists() else []
    if cached:
        target = cached[-1]
        suffix = target.suffix.lower()
        content_type = {
            ".pdf": "application/pdf",
            ".doc": "application/msword",
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".xls": "application/vnd.ms-excel",
            ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        }.get(suffix, "text/html")
        return {
            "source_id": source_id,
            "path": str(target),
            "source_file": target.relative_to(project_root).as_posix(),
            "source_url": url,
            "parent_url": url,
            "content_type": content_type,
            "source_sha256": sha256_file(target),
            "fetched_at_utc": "",
            "http_status": "CACHED",
            "error": "",
        }
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; official-list-research/1.0)",
            "Accept": "text/html,application/pdf,application/octet-stream;q=0.9,*/*;q=0.8",
            "Accept-Encoding": "gzip",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read()
            content_encoding = response.headers.get("Content-Encoding", "").lower()
            if content_encoding == "gzip" and payload[:2] == b"\x1f\x8b":
                payload = gzip.decompress(payload)
            content_type = response.headers.get("Content-Type", "")
            final_url = response.geturl()
            http_status = str(response.status)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {
            "source_id": source_id,
            "source_url": url,
            "http_status": "",
            "error": f"{type(exc).__name__}: {exc}",
        }
    digest = hashlib.sha256(payload).hexdigest()
    parsed = urllib.parse.urlparse(final_url)
    suffix = Path(parsed.path).suffix.lower()
    if suffix not in {".pdf", ".html", ".htm", ".shtml", ".doc", ".docx", ".xls", ".xlsx"}:
        suffix = ".pdf" if "pdf" in content_type.lower() or payload.startswith(b"%PDF") else ".html"
    stem = requested_stem
    rel = Path("03_external_raw") / source_id / f"{digest[:12]}__{stem}{suffix}"
    target = project_root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_bytes(payload)
    return {
        "source_id": source_id,
        "path": str(target),
        "source_file": rel.as_posix(),
        "source_url": final_url,
        "parent_url": url,
        "content_type": content_type,
        "source_sha256": digest,
        "fetched_at_utc": "",
        "http_status": http_status,
        "error": "",
    }


def official_attachment_urls(source: dict[str, str]) -> list[str]:
    """Discover declared attachments in cached MIIT/CNIPA notice HTML."""
    path = Path(source["path"])
    if path.suffix.lower() not in {".html", ".htm", ".shtml"}:
        return []
    payload = path.read_bytes()
    if payload[:2] == b"\x1f\x8b":
        payload = gzip.decompress(payload)
    page = payload.decode("utf-8", errors="replace")
    base = source.get("source_url", "") or source.get("parent_url", "")
    host = urllib.parse.urlparse(base).hostname or ""
    if host.endswith("miit.gov.cn"):
        canonical_base = "https://www.miit.gov.cn/"
        allowed = "miit.gov.cn"
    elif host.endswith("cnipa.gov.cn"):
        canonical_base = "https://www.cnipa.gov.cn/"
        allowed = "cnipa.gov.cn"
    else:
        return []
    raw_candidates = [
        html.unescape(match.group(1))
        for match in re.finditer(
            r"(?is)(?:href|src|fileurl)\s*=\s*[\"']([^\"']+)[\"']",
            page,
        )
    ]
    output = []
    for raw in raw_candidates:
        parsed_raw = urllib.parse.urlparse(raw)
        nested = urllib.parse.parse_qs(parsed_raw.query)
        candidate = ""
        for key in ("fileUrl", "file"):
            if nested.get(key):
                candidate = nested[key][0]
                break
        if candidate:
            absolute = urllib.parse.urljoin(canonical_base, candidate)
        else:
            absolute = urllib.parse.urljoin(canonical_base, raw)
        parsed = urllib.parse.urlparse(absolute)
        if not (parsed.hostname or "").endswith(allowed):
            continue
        query_values = " ".join(value for values in urllib.parse.parse_qs(parsed.query).values() for value in values)
        indicator = urllib.parse.unquote(parsed.path + " " + query_values).lower()
        if not re.search(r"\.(?:pdf|docx?|xlsx?)(?:\b|$)", indicator):
            continue
        if absolute not in output:
            output.append(absolute)
    return output


def match_record(
    geography: Geography,
    province_raw: str,
    location_raw: str,
    mapping_text: str,
) -> dict[str, str]:
    match = geography.matcher.match(province_raw, location_raw, mapping_text)
    usable = (
        match.prefecture_code in geography.city_by_code
        and match.status in {"matched_exact", "matched_flagged"}
    )
    city = geography.city_by_code.get(match.prefecture_code, {}) if usable else {}
    return {
        "city_code": city.get("city_code", ""),
        "city_name": city.get("city_name", ""),
        "province_code": city.get("province_code", match.province_code),
        "province_name": city.get("province_name", match.province_name),
        "match_status": match.status,
        "match_method": match.method,
        "match_evidence": match.evidence,
        "usable_for_panel": "1" if usable else "0",
        "qc_flags": match.flags,
    }


def make_record(
    *,
    task: str,
    source: dict[str, str],
    year: int,
    measure: str,
    list_status: str,
    item_number: Any,
    province_raw: str,
    location_raw: str,
    entity_name: str,
    item_text: str,
    extraction_method: str,
    geography: Geography,
    note: str = "",
) -> dict[str, Any]:
    mapping_text = clean(" ".join((location_raw, entity_name, item_text)))
    record = {
        "task": task,
        "source_id": source.get("source_id", ""),
        "year": year,
        "measure": measure,
        "list_status": list_status,
        "item_number": item_number,
        "province_raw": clean(province_raw),
        "location_raw": clean(location_raw),
        "entity_name": clean(entity_name),
        "item_text": clean(item_text),
        "source_file": source.get("source_file", ""),
        "source_url": source.get("source_url", ""),
        "source_sha256": source.get("source_sha256", ""),
        "extraction_method": extraction_method,
        "mapping_text": mapping_text,
        "note": note,
    }
    record.update(match_record(geography, province_raw, location_raw, mapping_text))
    record["record_id"] = stable_id(
        task,
        record["source_id"],
        year,
        measure,
        list_status,
        item_number,
        entity_name,
        record["source_file"],
        record["city_code"],
        record["match_status"],
    )
    return record
