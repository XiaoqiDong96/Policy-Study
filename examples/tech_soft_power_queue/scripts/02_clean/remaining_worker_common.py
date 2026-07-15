#!/usr/bin/env python3
"""Small, dependency-light helpers shared by the CR10--CR15 workers.

The helpers deliberately keep evidence and observations separate.  In
particular, a missing city-year is never converted to a measured zero unless a
worker can establish that the relevant national list or source universe was
observed for that year.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable


YEARS = tuple(range(2012, 2027))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def compact(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip()


def norm_code(value: Any) -> str:
    text = clean(value)
    if text.endswith(".0"):
        text = text[:-2]
    return text.zfill(6) if text.isdigit() else text


def stable_id(*parts: Any, length: int = 24) -> str:
    raw = "\x1f".join(clean(part) for part in parts).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:length]


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: Iterable[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})
    temporary.replace(path)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def read_cities(project: Path) -> list[dict[str, str]]:
    path = project / "04_crosswalk" / "city_master_297_snapshot.csv"
    rows = read_csv(path)
    codes = {norm_code(row.get("city_code")) for row in rows}
    if len(rows) != 297 or len(codes) != 297:
        raise RuntimeError(f"Formal city master must contain 297 unique cities: {path}")
    for row in rows:
        row["city_code"] = norm_code(row.get("city_code"))
        row["province_code"] = norm_code(row.get("province_code"))
    return rows


class CityMatcher:
    """Conservative exact-name matcher for the formal 297-city universe."""

    def __init__(self, cities: list[dict[str, str]]) -> None:
        self.cities = cities
        self.by_code = {row["city_code"]: row for row in cities}
        aliases: list[tuple[str, dict[str, str], str]] = []
        for row in cities:
            name = compact(row.get("city_name"))
            candidates = [(name, "full_city_name")]
            if name.endswith("市") and len(name[:-1]) >= 2:
                candidates.append((name[:-1], "city_name_without_suffix"))
            for alias, method in candidates:
                if alias:
                    aliases.append((alias, row, method))
        self.aliases = sorted(aliases, key=lambda item: (-len(item[0]), item[0]))

    def all(self, text: Any) -> list[dict[str, str]]:
        value = compact(text)
        found: dict[str, dict[str, str]] = {}
        occupied: list[tuple[int, int]] = []
        for alias, city, method in self.aliases:
            start = value.find(alias)
            if start < 0:
                continue
            end = start + len(alias)
            if method == "city_name_without_suffix":
                # Short aliases are accepted only when immediately followed by
                # an administrative/location token.  This avoids matching
                # ordinary words such as "吉林" or "开封" out of context.
                tail = value[end : end + 3]
                if not tail.startswith(("市", "地区", "州", "区", "县")):
                    continue
            if any(not (end <= left or start >= right) for left, right in occupied):
                continue
            found[city["city_code"]] = {**city, "match_alias": alias, "match_method": method}
            occupied.append((start, end))
        return sorted(found.values(), key=lambda row: row["city_code"])

    def one(self, text: Any) -> dict[str, str] | None:
        matches = self.all(text)
        return matches[0] if len(matches) == 1 else None


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href = ""
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "a":
            self._href = dict(attrs).get("href") or ""
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._href:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href:
            self.links.append((self._href, clean("".join(self._parts))))
            self._href = ""
            self._parts = []


def extract_links(html: bytes) -> list[tuple[str, str]]:
    text = html.decode("utf-8", errors="replace")
    parser = LinkParser()
    parser.feed(text)
    return parser.links


def fetch_url(url: str, timeout: int = 45) -> tuple[bytes, dict[str, Any]]:
    parts = urllib.parse.urlsplit(url)
    encoded_url = urllib.parse.urlunsplit(
        (
            parts.scheme,
            parts.netloc.encode("idna").decode("ascii"),
            urllib.parse.quote(urllib.parse.unquote(parts.path), safe="/%:@"),
            urllib.parse.quote(urllib.parse.unquote(parts.query), safe="=&/%:@?"),
            urllib.parse.quote(urllib.parse.unquote(parts.fragment), safe=""),
        )
    )
    request = urllib.request.Request(
        encoded_url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; TechSoftPowerResearch/1.0; evidence-audit)",
            "Accept": "text/html,application/xhtml+xml,application/pdf,application/octet-stream,*/*",
            "Referer": "https://www.miit.gov.cn/",
        },
    )
    started = utc_now()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            metadata = {
                "requested_url": url,
                "final_url": response.geturl(),
                "http_status": int(response.status),
                "content_type": response.headers.get("Content-Type", ""),
                "content_disposition": response.headers.get("Content-Disposition", ""),
                "bytes": len(body),
                "sha256": sha256_bytes(body),
                "fetched_at_utc": started,
                "error": "",
            }
            return body, metadata
    except urllib.error.HTTPError as exc:
        body = exc.read()
        return body, {
            "requested_url": url,
            "final_url": exc.geturl(),
            "http_status": int(exc.code),
            "content_type": exc.headers.get("Content-Type", "") if exc.headers else "",
            "content_disposition": "",
            "bytes": len(body),
            "sha256": sha256_bytes(body),
            "fetched_at_utc": started,
            "error": f"HTTPError: {exc}",
        }
    except Exception as exc:
        return b"", {
            "requested_url": url,
            "final_url": "",
            "http_status": 0,
            "content_type": "",
            "content_disposition": "",
            "bytes": 0,
            "sha256": "",
            "fetched_at_utc": started,
            "error": f"{type(exc).__name__}: {exc}",
        }


def pdf_text(path: Path) -> tuple[str, str]:
    """Extract PDF text, preferring layout-preserving pdftotext."""
    try:
        completed = subprocess.run(
            ["pdftotext", "-layout", str(path), "-"],
            check=True,
            capture_output=True,
            timeout=180,
        )
        text = completed.stdout.decode("utf-8", errors="replace")
        if clean(text):
            return text, "pdftotext_layout"
    except Exception:
        pass
    try:
        from build_full_collection_text_index import extract_text

        return extract_text(path, "application/pdf")
    except Exception as exc:
        return "", f"unextractable:{type(exc).__name__}:{exc}"


def split_numbered_rows(text: str) -> list[str]:
    """Split layout text at likely table row numbers without inventing rows."""
    normalized = text.replace("\r", "\n")
    marks = list(re.finditer(r"(?m)^\s*(\d{1,3})\s+(?=[\u4e00-\u9fff])", normalized))
    if len(marks) < 2:
        marks = list(re.finditer(r"(?<!\d)(\d{1,3})\s+(?=[\u4e00-\u9fff])", normalized))
    rows: list[str] = []
    for index, mark in enumerate(marks):
        end = marks[index + 1].start() if index + 1 < len(marks) else len(normalized)
        value = clean(normalized[mark.start() : end])
        if 4 <= len(value) <= 5000:
            rows.append(value)
    return rows


def panel_base(cities: list[dict[str, str]]) -> list[dict[str, Any]]:
    return [
        {
            "city_code": city["city_code"],
            "city_name": city.get("city_name", ""),
            "province_code": city.get("province_code", ""),
            "province_name": city.get("province_name", ""),
            "year": year,
        }
        for city in cities
        for year in YEARS
    ]
