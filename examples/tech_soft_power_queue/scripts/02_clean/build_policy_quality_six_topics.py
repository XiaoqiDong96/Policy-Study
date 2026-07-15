#!/usr/bin/env python3
"""CR14: build six transparent policy-quality topic packs and city-year panel.

The worker uses completed policy-document outputs, not unreviewed web snippets.
Topic assignment is deterministic keyword evidence over titles, model evidence,
targets, measures, and implementation fields.  Province-level documents are
retained in the document pack but never broadcast to all cities in a province.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path
from typing import Any, Iterable

from remaining_worker_common import (
    clean,
    norm_code,
    panel_base,
    read_cities,
    stable_id,
    utc_now,
    write_csv,
    write_json,
)


PROJECT = Path(__file__).resolve().parents[2]
DOCUMENTS = Path("05_intermediate/policy_quality_six_topic_documents.csv")
JOINT_EDGES = Path("05_intermediate/policy_joint_issuance_department_edges.csv")
PANEL = Path("06_panel/policy_quality_six_topic_297_city_year_2012_2026.csv")
SUMMARY = Path("10_qc/policy_quality_six_topic_summary.json")
COVERAGE = Path("10_qc/policy_quality_six_topic_coverage.csv")

TOPICS = {
    "green": (
        "绿色", "低碳", "碳达峰", "碳中和", "节能", "环保", "生态", "循环经济",
        "清洁生产", "污染防治", "资源节约", "气候", "可持续",
    ),
    "human_care": (
        "民生", "人文", "公共服务", "老年", "养老", "残疾", "无障碍", "健康",
        "医疗", "安全", "就业", "公平", "普惠", "弱势", "儿童", "妇女",
    ),
    "science_communication": (
        "科普", "科学传播", "科学普及", "科学素质", "科学素养", "科技馆", "博物馆",
        "公众参与", "科学教育", "开放日",
    ),
    "incubation": (
        "孵化器", "孵化", "众创空间", "创业", "加速器", "科技园", "创客", "小微企业",
        "创新创业", "创业服务",
    ),
    "industry_university": (
        "产学研", "校企", "协同育人", "高校", "大学", "联合实验室", "技术转移",
        "成果转化", "实训基地", "产教融合", "协同创新",
    ),
    "culture_technology": (
        "文化科技", "科技文化", "数字文化", "文化产业", "文化创意", "文旅", "非遗",
        "文化和旅游", "文化数字化", "创意设计", "工业文化", "文化装备",
    ),
}

SOURCE_SPECS = [
    ("culture_industry", "TECH", "03_external_raw/policy_panels/culture_industry/culture_industry_policy_documents.csv"),
    ("artificial_intelligence", "POLICY", "outputs/ai_policy_panel/final_refined_panels/ai_policy_documents_refined.csv"),
    ("future_industries", "POLICY", "outputs/future_industries_policy_panel/final_panels/future_industries_policy_documents.csv"),
    ("low_altitude", "POLICY", "outputs/low_altitude_policy_panel/final_panels/low_altitude_economy_policy_documents.csv"),
    ("new_energy_vehicle", "POLICY", "outputs/nev_policy_panel/final_full_qwen3p5_panels/final_nev_policy_documents.csv"),
]

FIELDS = [
    "topic_record_id", "source_domain", "source_document_id", "topic", "title", "year",
    "province", "admin_level", "admin_city", "city_adcode", "city_code", "city_name",
    "formal_297_city_eligible", "keyword_hits", "document_count_component",
    "tool_breadth", "policy_tools", "tool_groups", "joint_issuance",
    "implementation_present", "specific_measure_present", "quality_dimension_count",
    "pub_depart", "strength_score", "coverage_breadth_score", "direct_target_evidence",
    "specific_measures", "implementation_mechanisms", "source_file", "qc_flags",
]

JOINT_EDGE_FIELDS = [
    "edge_id", "source_domain", "source_document_id", "year", "city_code",
    "city_name", "formal_297_city_eligible", "department_a", "department_b",
    "pub_depart", "topic_hits", "source_file",
]


def csv_rows(path: Path) -> Iterable[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        yield from csv.DictReader(handle)


def tokens(value: Any) -> list[str]:
    text = clean(value)
    if not text:
        return []
    return sorted({part.strip() for part in re.split(r"[|;,，；、]+", text) if part.strip()})


def document_year(row: dict[str, str]) -> int | None:
    value = " ".join(clean(row.get(name)) for name in ("date_month", "pub_date", "use_date", "year"))
    match = re.search(r"20(?:1[2-9]|2[0-6])", value)
    return int(match.group()) if match else None


def joint_issuance(value: str) -> int:
    return int(bool(explicit_joint_departments(value)))


def explicit_joint_departments(value: str) -> list[str]:
    """Accept only short, explicit issuer lists; reject aggregated/corrupt fields."""
    text = re.sub(r"（[^）]*）|\([^)]*\)", "", clean(value))
    raw = [
        part.strip()
        for part in re.split(r"[|;,，；、]+", text)
        if part.strip() and not re.search(r"其他机构|有关部门", part)
    ]
    if not 2 <= len(raw) <= 5:
        return []
    if len(set(raw)) != len(raw):
        return []
    if any(
        part.endswith("等")
        or re.search(r"等\d+部门", part)
        or not 2 <= len(part) <= 60
        for part in raw
    ):
        return []
    return sorted(raw)


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
    culture = project / SOURCE_SPECS[0][2]
    culture_manifest = project / "10_qc" / "culture_industry_policy_manifest.json"
    if not culture.is_file() or not culture_manifest.is_file():
        print(f"WAITING_EXTERNAL: CR00 completed culture documents are absent: {culture}")
        return 75
    manifest_value = json.loads(culture_manifest.read_text(encoding="utf-8"))
    if manifest_value.get("status") != "PASS":
        print(f"WAITING_EXTERNAL: CR00 culture manifest is not PASS: {culture_manifest}")
        return 75
    cities = read_cities(project)
    city_by_code = {row["city_code"]: row for row in cities}
    city_by_name = {clean(row["city_name"]): row for row in cities}
    for row in cities:
        name = clean(row["city_name"])
        if name.endswith("市"):
            city_by_name.setdefault(name[:-1], row)

    sources: list[tuple[str, Path]] = []
    for domain, location, relative in SOURCE_SPECS:
        path = (project if location == "TECH" else policy_project) / relative
        if path.is_file():
            sources.append((domain, path))
    if not sources or sources[0][0] != "culture_industry":
        raise RuntimeError("Completed culture-industry policy documents are required")

    output: list[dict[str, Any]] = []
    joint_edges: list[dict[str, Any]] = []
    joint_edge_keys: set[tuple[str, str, str, str]] = set()
    baseline_city_year: set[tuple[str, int]] = set()
    source_counts: Counter[str] = Counter()
    for domain, path in sources:
        for row in csv_rows(path):
            source_counts[domain] += 1
            year = document_year(row)
            code = norm_code(row.get("city_adcode", ""))
            city = city_by_code.get(code)
            admin_city = clean(row.get("admin_city", ""))
            if not city and admin_city:
                city = city_by_name.get(admin_city) or city_by_name.get(admin_city.removesuffix("市"))
                code = city["city_code"] if city else ""
            # Only explicit prefecture/municipality assignments are formal.
            formal = int(bool(city and year))
            if formal:
                baseline_city_year.add((str(code), int(year)))
            evidence_fields = (
                "title", "direct_target_evidence", "measure_or_guidance_evidence",
                "target_segments", "specific_measures", "implementation_mechanisms",
                "policy_tools", "tool_groups",
            )
            evidence = " ".join(clean(row.get(field, "")) for field in evidence_fields)
            matched_topics: dict[str, list[str]] = {}
            for topic, keywords in TOPICS.items():
                hits = sorted({keyword for keyword in keywords if keyword in evidence}, key=lambda value: (-len(value), value))
                if hits:
                    matched_topics[topic] = hits
            if not matched_topics:
                continue
            tools = tokens(row.get("policy_tools"))
            groups = tokens(row.get("tool_groups"))
            breadth = len(set(tools + groups))
            implementation = int(bool(tokens(row.get("implementation_mechanisms"))))
            specific = int(
                clean(row.get("measure_specificity")) == "specific_measures"
                or bool(tokens(row.get("specific_measures")))
            )
            departments = explicit_joint_departments(row.get("pub_depart", ""))
            joint = int(bool(departments))
            source_id = clean(row.get("id")) or stable_id(domain, row.get("title"), row.get("pub_date"))
            source_file = str(path.relative_to(project if str(path).startswith(str(project)) else policy_project))
            for department_a, department_b in combinations(departments, 2):
                department_a, department_b = sorted((department_a, department_b))
                edge_key = (domain, source_id, department_a, department_b)
                if edge_key in joint_edge_keys:
                    continue
                joint_edge_keys.add(edge_key)
                joint_edges.append({
                    "edge_id": stable_id("policy_joint_department", *edge_key),
                    "source_domain": domain,
                    "source_document_id": source_id,
                    "year": year or "",
                    "city_code": code if city else "",
                    "city_name": city.get("city_name", "") if city else "",
                    "formal_297_city_eligible": formal,
                    "department_a": department_a,
                    "department_b": department_b,
                    "pub_depart": clean(row.get("pub_depart")),
                    "topic_hits": "|".join(sorted(matched_topics)),
                    "source_file": source_file,
                })
            for topic, hits in matched_topics.items():
                flags = []
                if not year:
                    flags.append("year_outside_2012_2026_or_missing")
                if not city:
                    flags.append("province_or_central_document_not_broadcast_to_city")
                output.append({
                    "topic_record_id": stable_id(domain, source_id, topic),
                    "source_domain": domain,
                    "source_document_id": source_id,
                    "topic": topic,
                    "title": clean(row.get("title")),
                    "year": year or "",
                    "province": clean(row.get("province") or row.get("admin_province")),
                    "admin_level": clean(row.get("admin_level")),
                    "admin_city": admin_city,
                    "city_adcode": norm_code(row.get("city_adcode", "")),
                    "city_code": code if city else "",
                    "city_name": city.get("city_name", "") if city else "",
                    "formal_297_city_eligible": formal,
                    "keyword_hits": "|".join(hits),
                    "document_count_component": 1,
                    "tool_breadth": breadth,
                    "policy_tools": "|".join(tools),
                    "tool_groups": "|".join(groups),
                    "joint_issuance": joint,
                    "implementation_present": implementation,
                    "specific_measure_present": specific,
                    "quality_dimension_count": int(breadth > 0) + joint + implementation + specific,
                    "pub_depart": clean(row.get("pub_depart")),
                    "strength_score": clean(row.get("strength_score")),
                    "coverage_breadth_score": clean(row.get("coverage_breadth_score")),
                    "direct_target_evidence": clean(row.get("direct_target_evidence")),
                    "specific_measures": clean(row.get("specific_measures")),
                    "implementation_mechanisms": clean(row.get("implementation_mechanisms")),
                    "source_file": source_file,
                    "qc_flags": "|".join(flags),
                })

    unique = {str(row["topic_record_id"]): row for row in output}
    output = sorted(unique.values(), key=lambda row: (str(row["topic"]), str(row["city_code"]), str(row["year"]), str(row["topic_record_id"])))
    write_csv(project / DOCUMENTS, output, FIELDS)
    joint_edges.sort(key=lambda row: (str(row["year"]), str(row["city_code"]), str(row["department_a"]), str(row["department_b"]), str(row["edge_id"])))
    write_csv(project / JOINT_EDGES, joint_edges, JOINT_EDGE_FIELDS)
    topic_counts = Counter(str(row["topic"]) for row in output)
    missing_topics = sorted(set(TOPICS) - set(topic_counts))
    if missing_topics:
        raise RuntimeError(f"Completed policy outputs produced no evidence rows for required topics: {missing_topics}")

    aggregates: defaultdict[tuple[str, int, str], Counter[str]] = defaultdict(Counter)
    for row in output:
        if not row["formal_297_city_eligible"]:
            continue
        key = (str(row["city_code"]), int(row["year"]), str(row["topic"]))
        aggregates[key]["document_count"] += 1
        aggregates[key]["tool_breadth_sum"] += int(row["tool_breadth"])
        aggregates[key]["joint_issuance_count"] += int(row["joint_issuance"])
        aggregates[key]["implementation_count"] += int(row["implementation_present"])
        aggregates[key]["specific_measure_count"] += int(row["specific_measure_present"])
        aggregates[key]["quality_dimension_sum"] += int(row["quality_dimension_count"])
    panel = panel_base(cities)
    metrics = (
        "document_count", "tool_breadth_sum", "joint_issuance_count",
        "implementation_count", "specific_measure_count", "quality_dimension_sum",
    )
    for row in panel:
        code, year = str(row["city_code"]), int(row["year"])
        observed = (code, year) in baseline_city_year
        row["policy_quality_selected_corpus_observed"] = int(observed)
        for topic in TOPICS:
            values = aggregates[(code, year, topic)]
            for metric in metrics:
                row[f"policy_{topic}_{metric}"] = values[metric] if observed else ""
    write_csv(project / PANEL, panel, list(panel[0]))

    coverage_rows = []
    for topic in TOPICS:
        rows = [row for row in output if row["topic"] == topic]
        coverage_rows.append({
            "topic": topic,
            "document_topic_rows": len(rows),
            "unique_source_documents": len({(row["source_domain"], row["source_document_id"]) for row in rows}),
            "formal_city_rows": sum(int(row["formal_297_city_eligible"]) for row in rows),
            "formal_cities": len({row["city_code"] for row in rows if row["formal_297_city_eligible"]}),
            "formal_years": len({row["year"] for row in rows if row["formal_297_city_eligible"]}),
        })
    write_csv(project / COVERAGE, coverage_rows, list(coverage_rows[0]))
    summary = {
        "status": "PASS",
        "task_id": "CR14",
        "generated_at_utc": utc_now(),
        "source_document_counts": dict(sorted(source_counts.items())),
        "source_files_used": len(sources),
        "topic_record_counts": dict(sorted(topic_counts.items())),
        "all_six_topics_present": len(topic_counts) == len(TOPICS),
        "topic_rows": len(output),
        "formal_city_topic_rows": sum(int(row["formal_297_city_eligible"]) for row in output),
        "explicit_joint_department_edges": len(joint_edges),
        "formal_city_joint_department_edges": sum(int(row["formal_297_city_eligible"]) for row in joint_edges),
        "joint_department_nodes": len({
            department
            for row in joint_edges
            for department in (row["department_a"], row["department_b"])
        }),
        "baseline_observed_city_years": len(baseline_city_year),
        "panel_rows": len(panel),
        "city_count": len({row["city_code"] for row in panel}),
        "dimensions": ["documents", "tool_breadth", "joint_issuance", "implementation", "specific_measures"],
        "province_policy_rule": "retained in document pack but never broadcast to prefecture cities",
        "zero_rule": "zero means no topic hit within selected completed policy corpora for an otherwise observed local city-year; unobserved cells are blank",
        "index_caution": "selected-domain policy corpus, not a census of all local policy documents; use components/candidate sensitivity, not an unqualified policy stock",
    }
    write_json(project / SUMMARY, summary)
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
