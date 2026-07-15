#!/usr/bin/env python3
"""Link official CAST guide entries to held-event articles and build a network.

Title linkage is deterministic and conservative.  Host cities always come from
the existing verified article-location extraction; guide titles never overwrite
an actual-event location.  All previously unmatched articles are copied into a
review file without city imputation.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from priority_worker_utils import PROJECT, YEARS, load_cities, read_csv, stable_id, write_csv


PLANNED = PROJECT / "05_intermediate" / "cast_conference_guide_planned_events.csv"
GUIDE_AVAILABILITY = PROJECT / "10_qc" / "cast_conference_guide_availability.csv"
ACTUAL = PROJECT / "05_intermediate" / "cast_academic_event_records.csv"
ACTUAL_UNMATCHED = PROJECT / "10_qc" / "cast_academic_event_unmatched.csv"
STATUS = PROJECT / "05_intermediate" / "cast_conference_plan_actual_status.csv"
MATCH_REVIEW = PROJECT / "10_qc" / "cast_conference_title_match_review_candidates.csv"
NODES = PROJECT / "05_intermediate" / "cast_conference_network_nodes.csv"
EDGES = PROJECT / "05_intermediate" / "cast_conference_network_edges.csv"
UNMATCHED_REVIEW = PROJECT / "10_qc" / "cr08_conference_actual_unmatched_review.csv"
PANEL = PROJECT / "06_panel" / "cast_conference_verified_network_297_city_year_2012_2026.csv"
SUMMARY = PROJECT / "10_qc" / "cast_conference_verified_network_summary.json"

STATUS_FIELDS = [
    "status_id", "record_type", "year", "planned_event_id", "actual_event_id",
    "source_actual_event_ids",
    "planned_title", "actual_title", "recommending_organization", "match_score",
    "match_method", "actual_city_codes", "actual_city_names", "verification_status",
]

NODE_FIELDS = [
    "node_id", "node_type", "node_name", "city_code", "city_name",
    "first_year", "last_year", "verified_event_count",
]

EDGE_FIELDS = [
    "edge_id", "source_node_id", "target_node_id", "source_node_type",
    "target_node_type", "event_count", "first_year", "last_year",
    "evidence_actual_event_ids", "relationship",
]

PANEL_FIELDS = [
    "city_code", "city_name", "province_code", "province_name", "year",
    "cast_actual_event_source_covered_year", "cast_guide_event_list_covered_year",
    "cast_verified_actual_event_count", "cast_guide_city_located_planned_event_count",
    "cast_plan_actual_verified_match_count", "cast_verified_host_organization_count",
    "cast_actual_event_with_explicit_organization_count",
    "cast_actual_event_explicit_organization_count",
]

ORGANIZATION_ROLE_PATTERN = "联合主办|主办|承办|协办"
ORGANIZATION_SUFFIX = re.compile(
    r"(?:学会|协会|大学|学院|研究院|研究所|中心|委员会|科协|"
    r"公司|集团|政府|厅|局|部|基金会|联盟|实验室|院)$"
)
ORGANIZATION_PATTERNS = (
    re.compile(rf"由([^，。；]{{2,80}}?)({ORGANIZATION_ROLE_PATTERN})"),
    re.compile(rf"(?:^|[，。；])([^，。；]{{2,60}}?)({ORGANIZATION_ROLE_PATTERN})"),
)


def normalized_title(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").lower()
    value = re.sub(r"(?:19|20)\d{2}年?", "", value)
    value = re.sub(r"[“”‘’\"'`~!@#$%^&*()_—–\-+=\[\]{}|\\:;：；，,。.、?？!！<>《》·\s]", "", value)
    value = re.sub(r"(?:隆重)?(?:召开|举办|开幕|闭幕|落幕|成功举办)$", "", value)
    return value


def normalized_description(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "")
    return re.sub(r"\s+", "", value)


def title_score(planned: str, actual: str) -> tuple[float, str]:
    left, right = normalized_title(planned), normalized_title(actual)
    if not left or not right:
        return 0.0, ""
    if left == right:
        return 1.0, "normalized_title_exact"
    ratio = SequenceMatcher(None, left, right).ratio()
    shorter, longer = sorted((left, right), key=len)
    if len(shorter) >= 10 and shorter in longer and ratio >= 0.72:
        return max(0.90, ratio), "normalized_title_containment"
    return ratio, "normalized_title_sequence_similarity"


def unique_events(rows: list[dict[str, str]]) -> dict[str, dict[str, Any]]:
    events: dict[str, dict[str, Any]] = {}
    for row in rows:
        event_id = row.get("event_id", "")
        if not event_id:
            continue
        event = events.setdefault(event_id, {
            "event_id": event_id, "title": row.get("title", ""),
            "event_year": int(row.get("event_year") or 0), "cities": {},
            "descriptions": set(),
            "source_event_ids": {event_id},
        })
        if row.get("city_code"):
            event["cities"][row["city_code"]] = row.get("city_name", "")
        if row.get("description"):
            event["descriptions"].add(row["description"])
    return events


def deduplicate_actual_events(events: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Collapse exact duplicate official articles into auditable event records."""
    result: dict[str, dict[str, Any]] = {}
    for source_id, event in events.items():
        description_key = "|".join(
            sorted(normalized_description(value) for value in event.get("descriptions", set()))
        )
        signature = (
            int(event.get("event_year") or 0),
            normalized_title(str(event.get("title", ""))),
            description_key or source_id,
        )
        canonical_id = stable_id("cast_actual_event", *signature)
        target = result.setdefault(canonical_id, {
            "event_id": canonical_id,
            "title": event.get("title", ""),
            "event_year": int(event.get("event_year") or 0),
            "cities": {},
            "descriptions": set(),
            "source_event_ids": set(),
        })
        if len(str(event.get("title", ""))) > len(str(target.get("title", ""))):
            target["title"] = event["title"]
        target["cities"].update(event.get("cities", {}))
        target["descriptions"].update(event.get("descriptions", set()))
        target["source_event_ids"].update(event.get("source_event_ids", {source_id}))
    return result


def explicit_actual_organizations(event: dict[str, Any]) -> dict[str, set[str]]:
    """Return only organizations explicitly followed by a host-role verb."""
    result: dict[str, set[str]] = defaultdict(set)
    for description in event.get("descriptions", set()):
        for pattern in ORGANIZATION_PATTERNS:
            for match in pattern.finditer(description):
                organization = match.group(1).strip(" ，。；:：“”")
                if "由" in organization:
                    organization = organization.rsplit("由", 1)[1]
                organization = re.sub(
                    r"^(?:本次|本届|本期|此次|该)(?:会议|论坛|活动|大会|沙龙)?",
                    "",
                    organization,
                ).strip()
                if ORGANIZATION_SUFFIX.search(organization):
                    members = [organization]
                    for separator in ("、", "和", "及", "与"):
                        refined: list[str] = []
                        for member in members:
                            parts = [part.strip() for part in member.split(separator) if part.strip()]
                            if len(parts) > 1 and all(ORGANIZATION_SUFFIX.search(part) for part in parts):
                                refined.extend(parts)
                            else:
                                refined.append(member)
                        members = refined
                    for member in members:
                        result[member].add(match.group(2))
    return result


def main() -> int:
    cities = load_cities()
    if not PLANNED.is_file():
        raise RuntimeError(f"CR07 output missing: {PLANNED}")
    planned_raw = read_csv(PLANNED)
    actual_raw = read_csv(ACTUAL)
    source_unmatched = read_csv(ACTUAL_UNMATCHED) if ACTUAL_UNMATCHED.is_file() else []

    planned: dict[str, dict[str, Any]] = {}
    for row in planned_raw:
        event = planned.setdefault(row["planned_event_id"], {
            "planned_event_id": row["planned_event_id"], "guide_year": int(row["guide_year"]),
            "event_name": row["event_name"], "recommending_organization": row["recommending_organization"],
            "cities": {},
        })
        if row.get("city_code"):
            event["cities"][row["city_code"]] = row.get("city_name", "")
    actual_source = unique_events(actual_raw)
    for row in source_unmatched:
        event_id = row.get("event_id", "")
        if event_id and event_id not in actual_source:
            year_match = re.search(r"/art/(20\d{2})/", row.get("article_url", ""))
            actual_source[event_id] = {
                "event_id": event_id, "title": row.get("title", ""),
                "event_year": int(row.get("event_year") or (year_match.group(1) if year_match else 0)),
                "cities": {},
                "descriptions": {row.get("description", "")} if row.get("description") else set(),
                "source_event_ids": {event_id},
            }
    actual = deduplicate_actual_events(actual_source)

    candidate_pairs: list[tuple[float, str, str, str]] = []
    review_pairs: list[tuple[float, str, str, str]] = []
    for planned_id, p_event in planned.items():
        for actual_id, a_event in actual.items():
            if p_event["guide_year"] != a_event["event_year"]:
                continue
            score, method = title_score(p_event["event_name"], a_event["title"])
            formally_verified = method in {"normalized_title_exact", "normalized_title_containment"} or score >= 0.95
            if formally_verified:
                candidate_pairs.append((score, method, planned_id, actual_id))
            elif score >= 0.88:
                review_pairs.append((score, method, planned_id, actual_id))
    candidate_pairs.sort(key=lambda item: (-item[0], item[2], item[3]))
    matched_planned: dict[str, tuple[str, float, str]] = {}
    matched_actual: dict[str, tuple[str, float, str]] = {}
    for score, method, planned_id, actual_id in candidate_pairs:
        if planned_id in matched_planned or actual_id in matched_actual:
            continue
        matched_planned[planned_id] = (actual_id, score, method)
        matched_actual[actual_id] = (planned_id, score, method)

    status_rows: list[dict[str, Any]] = []
    for planned_id, p_event in sorted(planned.items()):
        match = matched_planned.get(planned_id)
        actual_id, score, method = match if match else ("", 0.0, "")
        a_event = actual.get(actual_id, {})
        city_codes = sorted(a_event.get("cities", {}))
        status_rows.append({
            "status_id": stable_id("planned", planned_id), "record_type": "planned_guide_event",
            "year": p_event["guide_year"], "planned_event_id": planned_id,
            "actual_event_id": actual_id, "planned_title": p_event["event_name"],
            "source_actual_event_ids": "|".join(sorted(a_event.get("source_event_ids", set()))),
            "actual_title": a_event.get("title", ""),
            "recommending_organization": p_event["recommending_organization"],
            "match_score": f"{score:.6f}" if match else "", "match_method": method,
            "actual_city_codes": "|".join(city_codes),
            "actual_city_names": "|".join(a_event.get("cities", {}).get(code, "") for code in city_codes),
            "verification_status": "title_verified_actual_with_host_city" if city_codes else "title_verified_actual_location_unavailable" if match else "planned_only_no_conservative_title_match",
        })
    for actual_id, a_event in sorted(actual.items()):
        match = matched_actual.get(actual_id)
        planned_id, score, method = match if match else ("", 0.0, "")
        p_event = planned.get(planned_id, {})
        city_codes = sorted(a_event.get("cities", {}))
        status_rows.append({
            "status_id": stable_id("actual", actual_id), "record_type": "actual_archive_event",
            "year": a_event["event_year"], "planned_event_id": planned_id,
            "actual_event_id": actual_id, "planned_title": p_event.get("event_name", ""),
            "source_actual_event_ids": "|".join(sorted(a_event.get("source_event_ids", set()))),
            "actual_title": a_event["title"],
            "recommending_organization": p_event.get("recommending_organization", ""),
            "match_score": f"{score:.6f}" if match else "", "match_method": method,
            "actual_city_codes": "|".join(city_codes),
            "actual_city_names": "|".join(a_event.get("cities", {}).get(code, "") for code in city_codes),
            "verification_status": "matched_to_official_guide" if match else "actual_only_no_conservative_guide_match",
        })

    match_review_rows: list[dict[str, Any]] = []
    for score, method, planned_id, actual_id in sorted(review_pairs, key=lambda item: (-item[0], item[2], item[3])):
        p_event, a_event = planned[planned_id], actual[actual_id]
        city_codes = sorted(a_event.get("cities", {}))
        match_review_rows.append({
            "status_id": stable_id("review", planned_id, actual_id),
            "record_type": "title_match_review_candidate", "year": p_event["guide_year"],
            "planned_event_id": planned_id, "actual_event_id": actual_id,
            "source_actual_event_ids": "|".join(sorted(a_event.get("source_event_ids", set()))),
            "planned_title": p_event["event_name"], "actual_title": a_event["title"],
            "recommending_organization": p_event["recommending_organization"],
            "match_score": f"{score:.6f}", "match_method": method,
            "actual_city_codes": "|".join(city_codes),
            "actual_city_names": "|".join(a_event.get("cities", {}).get(code, "") for code in city_codes),
            "verification_status": "review_only_not_used_in_formal_network",
        })

    edge_events: dict[tuple[str, str, str], list[tuple[int, str]]] = defaultdict(list)
    organization_events: dict[str, list[tuple[int, str]]] = defaultdict(list)
    city_events: dict[str, list[tuple[int, str]]] = defaultdict(list)
    guide_organizations: set[str] = set()
    actual_organizations: set[str] = set()
    actual_events_with_explicit_organization: set[str] = set()
    for planned_id, (actual_id, _, _) in matched_planned.items():
        p_event, a_event = planned[planned_id], actual[actual_id]
        organization = p_event["recommending_organization"]
        if not organization:
            continue
        guide_organizations.add(organization)
        organization_events[organization].append((p_event["guide_year"], actual_id))
        for city_code in a_event["cities"]:
            edge_events[(
                organization,
                city_code,
                "official_guide_recommending_organization_to_verified_actual_host_city",
            )].append((p_event["guide_year"], actual_id))
            city_events[city_code].append((p_event["guide_year"], actual_id))

    actual_event_organizations: dict[str, set[str]] = defaultdict(set)
    for actual_id, a_event in actual.items():
        organizations = explicit_actual_organizations(a_event)
        if organizations:
            actual_events_with_explicit_organization.add(actual_id)
        for organization, roles in organizations.items():
            actual_organizations.add(organization)
            actual_event_organizations[actual_id].add(organization)
            organization_events[organization].append((a_event["event_year"], actual_id))
            for city_code in a_event["cities"]:
                city_events[city_code].append((a_event["event_year"], actual_id))
                for role in sorted(roles):
                    edge_events[(
                        organization,
                        city_code,
                        f"official_actual_report_explicit_{role}_organization_to_host_city",
                    )].append((a_event["event_year"], actual_id))

    nodes: list[dict[str, Any]] = []
    organization_node_ids: dict[str, str] = {}
    city_node_ids: dict[str, str] = {}
    for organization, evidence in sorted(organization_events.items()):
        node_id = stable_id("organization", organization)
        organization_node_ids[organization] = node_id
        years = [year for year, _ in evidence]
        nodes.append({
            "node_id": node_id, "node_type": "evidence_organization_string",
            "node_name": organization, "first_year": min(years), "last_year": max(years),
            "verified_event_count": len({event_id for _, event_id in evidence}),
        })
    city_by_code = {row["city_code"]: row for row in cities}
    for city_code, evidence in sorted(city_events.items()):
        node_id = stable_id("city", city_code)
        city_node_ids[city_code] = node_id
        years = [year for year, _ in evidence]
        nodes.append({
            "node_id": node_id, "node_type": "host_city", "node_name": city_by_code[city_code]["city_name"],
            "city_code": city_code, "city_name": city_by_code[city_code]["city_name"],
            "first_year": min(years), "last_year": max(years),
            "verified_event_count": len({event_id for _, event_id in evidence}),
        })
    edges: list[dict[str, Any]] = []
    for (organization, city_code, relationship), evidence in sorted(edge_events.items()):
        years = [year for year, _ in evidence]
        event_ids = sorted({event_id for _, event_id in evidence})
        edges.append({
            "edge_id": stable_id("organization_host_city", organization, city_code, relationship),
            "source_node_id": organization_node_ids[organization],
            "target_node_id": city_node_ids[city_code],
            "source_node_type": "evidence_organization_string", "target_node_type": "host_city",
            "event_count": len(event_ids), "first_year": min(years), "last_year": max(years),
            "evidence_actual_event_ids": "|".join(event_ids),
            "relationship": relationship,
        })

    actual_covered_years = {event["event_year"] for event in actual.values() if event["event_year"] in YEARS}
    guide_complete_years: set[int] = set()
    if GUIDE_AVAILABILITY.is_file():
        for row in read_csv(GUIDE_AVAILABILITY):
            if row.get("event_level_list_status") == "parsed_complete":
                guide_complete_years.add(int(row["guide_year"]))

    actual_by_city_year: dict[tuple[str, int], set[str]] = defaultdict(set)
    planned_by_city_year: dict[tuple[str, int], set[str]] = defaultdict(set)
    match_by_city_year: dict[tuple[str, int], set[str]] = defaultdict(set)
    organizations_by_city_year: dict[tuple[str, int], set[str]] = defaultdict(set)
    actual_events_with_org_by_city_year: dict[tuple[str, int], set[str]] = defaultdict(set)
    actual_orgs_by_city_year: dict[tuple[str, int], set[str]] = defaultdict(set)
    for event in actual.values():
        for city_code in event["cities"]:
            actual_by_city_year[(city_code, event["event_year"])].add(event["event_id"])
    for event in planned.values():
        for city_code in event["cities"]:
            planned_by_city_year[(city_code, event["guide_year"])].add(event["planned_event_id"])
    for planned_id, (actual_id, _, _) in matched_planned.items():
        p_event, a_event = planned[planned_id], actual[actual_id]
        for city_code in a_event["cities"]:
            match_by_city_year[(city_code, p_event["guide_year"])].add(actual_id)
            if p_event["recommending_organization"]:
                organizations_by_city_year[(city_code, p_event["guide_year"])].add(p_event["recommending_organization"])
    for actual_id, organizations in actual_event_organizations.items():
        a_event = actual[actual_id]
        for city_code in a_event["cities"]:
            key = (city_code, a_event["event_year"])
            actual_events_with_org_by_city_year[key].add(actual_id)
            actual_orgs_by_city_year[key].update(organizations)
            organizations_by_city_year[key].update(organizations)

    panel_rows: list[dict[str, Any]] = []
    for city in cities:
        for year in YEARS:
            actual_covered = year in actual_covered_years
            guide_covered = year in guide_complete_years
            key = (city["city_code"], year)
            panel_rows.append({
                "city_code": city["city_code"], "city_name": city["city_name"],
                "province_code": city["province_code"], "province_name": city["province_name"],
                "year": year, "cast_actual_event_source_covered_year": int(actual_covered),
                "cast_guide_event_list_covered_year": int(guide_covered),
                "cast_verified_actual_event_count": len(actual_by_city_year[key]) if actual_covered else "",
                "cast_guide_city_located_planned_event_count": len(planned_by_city_year[key]) if guide_covered else "",
                "cast_plan_actual_verified_match_count": len(match_by_city_year[key]) if guide_covered else "",
                "cast_verified_host_organization_count": len(organizations_by_city_year[key]) if actual_covered else "",
                "cast_actual_event_with_explicit_organization_count": len(actual_events_with_org_by_city_year[key]) if actual_covered else "",
                "cast_actual_event_explicit_organization_count": len(actual_orgs_by_city_year[key]) if actual_covered else "",
            })
    if len(panel_rows) != 4_455:
        raise RuntimeError(f"297 x 15 panel gate failed: {len(panel_rows)}")

    review_fields = list(source_unmatched[0].keys()) if source_unmatched else ["event_id", "title", "event_year", "reason"]
    review_fields += ["review_status"]
    unmatched_review_rows = [{**row, "review_status": "retained_unmatched_no_city_imputation"} for row in source_unmatched]

    write_csv(STATUS, status_rows, STATUS_FIELDS)
    write_csv(MATCH_REVIEW, match_review_rows, STATUS_FIELDS)
    write_csv(NODES, nodes, NODE_FIELDS)
    write_csv(EDGES, edges, EDGE_FIELDS)
    write_csv(UNMATCHED_REVIEW, unmatched_review_rows, review_fields)
    write_csv(PANEL, panel_rows, PANEL_FIELDS)
    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "PASS" if actual and planned and len(unmatched_review_rows) == len(source_unmatched) and len(panel_rows) == 4455 else "FAIL",
        "planned_events": len(planned),
        "actual_archive_articles": sum(len(event["source_event_ids"]) for event in actual.values()),
        "actual_archive_events": len(actual),
        "exact_duplicate_actual_articles_collapsed": sum(len(event["source_event_ids"]) - 1 for event in actual.values()),
        "conservative_plan_actual_matches": len(matched_planned),
        "review_only_title_match_candidates_0_88_to_0_95": len(match_review_rows),
        "matches_with_verified_host_city": sum(bool(actual[actual_id]["cities"]) for actual_id, _, _ in matched_planned.values()),
        "actual_events_with_verified_host_city": sum(bool(event["cities"]) for event in actual.values()),
        "actual_events_with_explicit_organization": len(actual_events_with_explicit_organization),
        "actual_events_with_explicit_organization_and_host_city": sum(
            bool(actual[event_id]["cities"]) for event_id in actual_events_with_explicit_organization
        ),
        "guide_recommending_organization_nodes": len(guide_organizations),
        "actual_explicit_organization_nodes": len(actual_organizations),
        "organization_nodes": len(organization_node_ids), "host_city_nodes": len(city_node_ids),
        "organization_host_city_edges": len(edges),
        "guide_recommending_organization_host_city_edges": sum(
            row["relationship"].startswith("official_guide_") for row in edges
        ),
        "actual_explicit_organization_host_city_edges": sum(
            row["relationship"].startswith("official_actual_report_") for row in edges
        ),
        "source_actual_unmatched_rows": len(source_unmatched),
        "preserved_actual_unmatched_review_rows": len(unmatched_review_rows),
        "panel_rows": len(panel_rows),
        "hard_gate": {
            "actual_host_city_not_overwritten_by_guide_title": True,
            "sequence_similarity_formal_threshold": 0.95,
            "containment_requires_normalized_shorter_title_length_at_least_10": True,
            "sequence_similarity_0_88_to_0_95_is_review_only": True,
            "one_to_one_matching": True,
            "all_source_unmatched_rows_preserved": len(unmatched_review_rows) == len(source_unmatched),
            "actual_organizations_require_explicit_role_verb": True,
        },
    }
    SUMMARY.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
