#!/usr/bin/env python3
"""CR11: combine incubation, cultural, green, and industrial-heritage events.

Every output count is an event flow in the event year.  The worker never
forward-fills a designation into later years and never infers revocation from
absence in a subsequent list.  Current state is computed only for entities
with an explicit ordered event trail.
"""

from __future__ import annotations

import argparse
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from remaining_worker_common import (
    clean,
    panel_base,
    read_cities,
    read_csv,
    stable_id,
    utc_now,
    write_csv,
    write_json,
)


PROJECT = Path(__file__).resolve().parents[2]
INPUTS = [
    ("incubation", Path("05_intermediate/priority_coworking_records.csv"), True),
    ("culture", Path("05_intermediate/priority_cr04_records.csv"), True),
    ("green", Path("05_intermediate/priority_cr09_records.csv"), True),
    ("industrial_heritage", Path("05_intermediate/industrial_heritage_lifecycle_records.csv"), True),
]
EVENTS = Path("05_intermediate/recognition_lifecycle_events.csv")
ENTITIES = Path("05_intermediate/recognition_lifecycle_entities.csv")
PANEL = Path("06_panel/recognition_lifecycle_297_city_year_2012_2026.csv")
SUMMARY = Path("10_qc/recognition_lifecycle_summary.json")

EVENT_FIELDS = [
    "lifecycle_event_id", "domain", "source_id", "source_record_id", "entity_key",
    "entity_name", "event_year", "event_type", "event_status", "city_code",
    "city_name", "province_code", "province_name", "formal_flow_eligible",
    "source_url", "source_file", "source_sha256", "raw_status_evidence", "qc_flags",
]


def first(row: dict[str, str], *names: str) -> str:
    for name in names:
        value = clean(row.get(name, ""))
        if value:
            return value
    return ""


def year_of(row: dict[str, str]) -> int | None:
    value = first(row, "event_year", "list_year", "designation_year", "year", "publication_year")
    match = re.search(r"20(?:1[2-9]|2[0-6])", value)
    return int(match.group()) if match else None


def event_of(domain: str, row: dict[str, str]) -> tuple[str, str]:
    explicit = first(row, "event_type", "lifecycle_event", "action_type")
    evidence = " ".join(
        first(row, name)
        for name in (
            "event_type", "list_status", "record_status", "status", "variable",
            "source_id", "raw_record", "entity_name",
        )
    )
    value = (explicit + " " + evidence).lower()
    if domain == "green" and "official_archive_document" in value:
        return "archive_incidence", evidence
    if domain == "culture" and re.search(r"creation_qualification|creation_list|创建资格|创建名单", value):
        return "creation_candidate", evidence
    if explicit in {
        "designation", "review_passed", "review_rectification", "revocation",
        "proposed_not_final", "archive_incidence", "creation_candidate",
    }:
        return explicit, evidence
    if re.search(r"撤销|取消|摘牌|作废|revok|withdraw", value):
        return "revocation", evidence
    if re.search(r"整改|未通过|rectif", value):
        return "review_rectification", evidence
    if re.search(r"复核|review", value):
        return "review_passed", evidence
    if re.search(r"拟认定|公示|proposed", value):
        return "proposed_not_final", evidence
    if re.search(r"正式.{0,12}(?:命名|认定)|(?:命名|认定)名单|designation|confirmed_announcement_flow", value):
        return "designation", evidence
    # CR01 is a fully parsed national announcement list with an explicit flow
    # contract. Other domains require explicit lifecycle language.
    if domain == "incubation" and "confirmed_announcement_flow" in value:
        return "designation", evidence
    return "unclassified", evidence


def eligible(row: dict[str, str], event_type: str) -> int:
    if event_type in {
        "proposed_not_final", "unclassified", "archive_incidence", "creation_candidate",
    }:
        return 0
    for name in ("formal_flow_eligible", "formal_variable_eligible", "formal_eligible"):
        value = clean(row.get(name, ""))
        if value:
            return int(value.lower() in {"1", "true", "yes", "eligible"})
    return int(bool(clean(row.get("city_code", ""))))


def normalize_entity(value: str) -> str:
    value = re.sub(r"[\s|,，。；;:：()（）\[\]【】]", "", value)
    value = re.sub(r"^(?:附件\d*|序号\d+)", "", value)
    return value[:200]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=PROJECT)
    args = parser.parse_args()
    project = args.project_root.resolve()
    cities = read_cities(project)
    city_codes = {row["city_code"] for row in cities}
    events: list[dict[str, Any]] = []
    input_counts: dict[str, int] = {}

    for domain, relative, required in INPUTS:
        path = project / relative
        if not path.is_file():
            if required:
                print(f"WAITING_EXTERNAL: required lifecycle input is absent: {path}")
                return 75
            continue
        rows = read_csv(path)
        input_counts[domain] = len(rows)
        for sequence, row in enumerate(rows, start=1):
            year = year_of(row)
            event_type, status_evidence = event_of(domain, row)
            city_code = clean(row.get("city_code", ""))
            entity_name = first(
                row, "entity_name", "base_name", "heritage_name", "project_name",
                "operator_name", "raw_record",
            )
            flags = []
            if not year:
                flags.append("missing_formal_event_year")
            if city_code and city_code not in city_codes:
                flags.append("outside_formal_297_city_universe")
                city_code = ""
            if not entity_name:
                flags.append("missing_entity_name")
            entity_norm = normalize_entity(entity_name)
            source_id = first(row, "source_id") or domain
            source_record_id = first(row, "record_id", "document_id") or stable_id(relative, sequence, entity_name)
            formal = eligible(row, event_type) if year else 0
            if formal and not city_code:
                formal = 0
                flags.append("formal_event_lacks_297_city")
            events.append({
                "lifecycle_event_id": stable_id(domain, source_id, source_record_id, year, event_type),
                "domain": domain,
                "source_id": source_id,
                "source_record_id": source_record_id,
                "entity_key": stable_id(domain, entity_norm) if entity_norm else "",
                "entity_name": entity_name,
                "event_year": year or "",
                "event_type": event_type,
                "event_status": "observed_official_event" if formal else "retained_nonformal_or_unmapped",
                "city_code": city_code,
                "city_name": first(row, "city_name"),
                "province_code": first(row, "province_code"),
                "province_name": first(row, "province_name"),
                "formal_flow_eligible": formal,
                "source_url": first(row, "source_url"),
                "source_file": first(row, "source_file"),
                "source_sha256": first(row, "source_sha256"),
                "raw_status_evidence": status_evidence,
                "qc_flags": "|".join(flags),
            })

    # Stable de-duplication by evidence-level event id.
    unique = {str(row["lifecycle_event_id"]): row for row in events}
    events = sorted(unique.values(), key=lambda row: (str(row["domain"]), str(row["entity_key"]), str(row["event_year"]), str(row["lifecycle_event_id"])))
    write_csv(project / EVENTS, events, EVENT_FIELDS)

    grouped: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in events:
        if row["entity_key"] and row["formal_flow_eligible"]:
            grouped[str(row["entity_key"])].append(row)
    entity_rows: list[dict[str, Any]] = []
    for entity_key, values in grouped.items():
        values.sort(key=lambda row: (int(row["event_year"]), str(row["event_type"])))
        latest = values[-1]
        state = {
            "designation": "active_from_observed_designation",
            "review_passed": "active_after_observed_review",
            "review_rectification": "rectification_required",
            "revocation": "inactive_after_explicit_revocation",
        }.get(str(latest["event_type"]), "unknown")
        entity_rows.append({
            "entity_key": entity_key,
            "domain": latest["domain"],
            "entity_name_latest": latest["entity_name"],
            "city_code_latest": latest["city_code"],
            "first_observed_event_year": min(int(row["event_year"]) for row in values),
            "latest_observed_event_year": int(latest["event_year"]),
            "latest_observed_event_type": latest["event_type"],
            "current_state_from_explicit_events": state,
            "observed_event_count": len(values),
            "state_caution": "no revocation inferred from later-list absence; no annual stock forward-fill",
        })
    entity_fields = list(entity_rows[0]) if entity_rows else [
        "entity_key", "domain", "entity_name_latest", "city_code_latest",
        "first_observed_event_year", "latest_observed_event_year",
        "latest_observed_event_type", "current_state_from_explicit_events",
        "observed_event_count", "state_caution",
    ]
    write_csv(project / ENTITIES, entity_rows, entity_fields)

    counts: Counter[tuple[str, int, str, str]] = Counter()
    observed: set[tuple[str, int]] = set()
    for row in events:
        if not row["formal_flow_eligible"]:
            continue
        domain = str(row["domain"])
        year = int(row["event_year"])
        observed.add((domain, year))
        counts[(str(row["city_code"]), year, domain, str(row["event_type"]))] += 1
    domains = ["incubation", "culture", "green", "industrial_heritage"]
    event_types = ["designation", "review_passed", "review_rectification", "revocation"]
    panel = panel_base(cities)
    for row in panel:
        code, year = str(row["city_code"]), int(row["year"])
        for domain in domains:
            domain_observed = (domain, year) in observed
            row[f"recognition_lifecycle_{domain}_source_observed"] = int(domain_observed)
            for event_type in event_types:
                field = f"recognition_lifecycle_{domain}_{event_type}_count"
                row[field] = counts[(code, year, domain, event_type)] if domain_observed else ""
    write_csv(project / PANEL, panel, list(panel[0]))

    event_counts = Counter(str(row["event_type"]) for row in events if row["formal_flow_eligible"])
    retained_event_counts = Counter(str(row["event_type"]) for row in events)
    summary = {
        "status": "PASS",
        "task_id": "CR11",
        "generated_at_utc": utc_now(),
        "required_inputs_present": len(input_counts),
        "input_record_counts": input_counts,
        "lifecycle_events": len(events),
        "formal_city_events": sum(int(row["formal_flow_eligible"]) for row in events),
        "formal_event_type_counts": dict(sorted(event_counts.items())),
        "all_retained_event_type_counts": dict(sorted(retained_event_counts.items())),
        "entity_lifecycle_rows": len(entity_rows),
        "panel_rows": len(panel),
        "city_count": len({row["city_code"] for row in panel}),
        "flow_stock_rule": "event-year flows only; designations are not forward-filled as annual stock",
        "revocation_rule": "revocation requires an explicit official event; later-list absence is not revocation",
        "archive_incidence_rule": "CR09 official_archive_document rows are documentary incidence, not designation flows",
        "culture_creation_rule": "2017 creation qualification and 2020 creation list are non-final candidates; only explicit final designation/review events are formal",
        "zero_rule": "zero only in domain-years with at least one formal row from the parsed national list/archive; otherwise blank",
    }
    write_json(project / SUMMARY, summary)
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
