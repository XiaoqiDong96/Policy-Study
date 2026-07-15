#!/usr/bin/env python3
"""Small dependency-light helpers shared by priority cloud workers."""

from __future__ import annotations

import csv
import hashlib
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable


PROJECT = Path(__file__).resolve().parents[2]
CITY_FILE = PROJECT / "04_crosswalk" / "city_master_297_snapshot.csv"
YEARS = range(2012, 2027)


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


def stable_id(*parts: Any, length: int = 24) -> str:
    value = "\x1f".join(str(part) for part in parts)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def compact(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip()


def load_cities() -> list[dict[str, str]]:
    cities = read_csv(CITY_FILE)
    if len(cities) != 297 or len({row["city_code"] for row in cities}) != 297:
        raise RuntimeError(f"city master is not the locked 297-city universe: {CITY_FILE}")
    return cities


class ConservativeCityLocator:
    """Match only explicit city strings; never infer a headquarters location."""

    _AMBIGUOUS_ALIASES = {
        "东方", "大同", "中山", "安宁", "临江", "海东", "江门",
        "普洱", "泰州", "宿州", "苏州", "榆林", "长治", "来宾",
    }

    def __init__(self, cities: list[dict[str, str]]) -> None:
        self.cities = cities
        self.by_code = {row["city_code"]: row for row in cities}
        self.full_names = sorted(cities, key=lambda row: len(row["city_name"]), reverse=True)
        candidates: dict[str, list[dict[str, str]]] = defaultdict(list)
        for city in cities:
            alias = re.sub(r"(?:市|地区|自治州|盟)$", "", city["city_name"])
            if len(alias) >= 2:
                candidates[alias].append(city)
        self.aliases = {
            alias: rows[0]
            for alias, rows in candidates.items()
            if len(rows) == 1 and alias not in self._AMBIGUOUS_ALIASES
        }
        self.explicit_alias_patterns = {
            alias: re.compile(
                rf"[（(]{re.escape(alias)}[）)]|^{re.escape(alias)}|(?:位于|落户|驻|在|于){re.escape(alias)}"
            )
            for alias in self.aliases
        }
        self.title_alias_patterns = {
            alias: re.compile(
                rf"(?:(?:^|中国|第[一二三四五六七八九十百0-9]*届|年|[（(·—\-在于]){re.escape(alias)}"
                rf"|{re.escape(alias)}(?=$|[）)·—\-]|国际|学术|科技|会议|论坛|大会|峰会|研讨|年会|活动|展览|船舶|精致))"
            )
            for alias in self.aliases
        }

    def locate(self, text: str, *, allow_title_alias: bool = False) -> list[tuple[dict[str, str], str, str]]:
        text = compact(text)
        found: dict[str, tuple[dict[str, str], str, str]] = {}
        for city in self.full_names:
            if city["city_name"] in text:
                found[city["city_code"]] = (city, "explicit_full_city_name", city["city_name"])
        for alias, city in sorted(self.aliases.items(), key=lambda item: len(item[0]), reverse=True):
            if city["city_code"] in found:
                continue
            explicit = bool(self.explicit_alias_patterns[alias].search(text))
            if allow_title_alias and self.title_alias_patterns[alias].search(text):
                explicit = True
            if explicit:
                found[city["city_code"]] = (city, "explicit_unique_suffix_free_city", alias)
        return list(found.values())


def blank_297_grid(
    cities: list[dict[str, str]],
    covered_years: set[int],
    aggregate: dict[tuple[str, int], dict[str, Any]],
    value_fields: list[str],
    coverage_field: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for city in cities:
        for year in YEARS:
            covered = year in covered_years
            row: dict[str, Any] = {
                "city_code": city["city_code"],
                "city_name": city["city_name"],
                "province_code": city["province_code"],
                "province_name": city["province_name"],
                "year": year,
                coverage_field: int(covered),
            }
            values = aggregate.get((city["city_code"], year), {})
            for field in value_fields:
                row[field] = values.get(field, 0) if covered else ""
            rows.append(row)
    if len(rows) != 4_455:
        raise RuntimeError(f"297 x 15 panel gate failed: {len(rows)} rows")
    return rows
