#!/usr/bin/env python3
"""Build province-level culture-industry category panels.

Input should be the merged final-yes JSONL after tool refinement, usually:
outputs/culture_industry_policy_panel/tool_refinement/culture_industry_final_yes_with_tools.jsonl
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple


CULTURE_CATEGORIES = [
    "文化产业综合",
    "新闻信息服务",
    "内容创作生产",
    "创意设计服务",
    "文化传播渠道",
    "文化投资运营",
    "文化娱乐休闲服务",
    "文化辅助生产和中介服务",
    "文化装备生产",
    "文化消费终端生产",
    "数字文化产业",
    "文化和旅游消费",
    "对外文化贸易",
    "文化金融",
    "文化产业园区/基地",
]

CANONICAL_ALIASES = {
    "文创产业": "创意设计服务",
    "文化创意产业": "创意设计服务",
    "影视产业": "内容创作生产",
    "电影产业": "内容创作生产",
    "出版产业": "内容创作生产",
    "版权产业": "内容创作生产",
    "动漫产业": "内容创作生产",
    "游戏产业": "内容创作生产",
    "演艺产业": "内容创作生产",
    "文旅消费": "文化和旅游消费",
    "文化消费": "文化和旅游消费",
    "文旅产业": "文化和旅游消费",
    "文化旅游产业": "文化和旅游消费",
    "文化贸易": "对外文化贸易",
    "文化出口": "对外文化贸易",
    "文化产业基金": "文化金融",
    "文化产业园区": "文化产业园区/基地",
    "文化产业基地": "文化产业园区/基地",
}

PROVINCES = [
    "北京市",
    "天津市",
    "河北省",
    "山西省",
    "内蒙古自治区",
    "辽宁省",
    "吉林省",
    "黑龙江省",
    "上海市",
    "江苏省",
    "浙江省",
    "安徽省",
    "福建省",
    "江西省",
    "山东省",
    "河南省",
    "湖北省",
    "湖南省",
    "广东省",
    "广西壮族自治区",
    "海南省",
    "重庆市",
    "四川省",
    "贵州省",
    "云南省",
    "西藏自治区",
    "陕西省",
    "甘肃省",
    "青海省",
    "宁夏回族自治区",
    "新疆维吾尔自治区",
]

TOOL_IDS = [
    "credit_finance",
    "tax_incentives",
    "equity_support",
    "fiscal_subsidies",
    "industrial_fund",
    "promote_entrepreneurship",
    "investment_policy",
    "business_environment",
    "market_access_regulation",
    "trade_protection",
    "labor_policy",
    "preferential_land_supply",
    "infrastructure_investment",
    "technology_rd_adoption",
    "environmental_policy",
    "consumer_subsidy",
    "government_procurement",
    "industrial_promotion",
    "industrial_cluster",
    "localization_policy",
]


def iter_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def canonical_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [str(v).strip() for v in parsed if str(v).strip()]
        except Exception:
            pass
        return [part.strip() for part in re.split(r"[;；,，、/]+", value) if part.strip()]
    return [str(value).strip()] if str(value).strip() else []


def month_from_row(row: Dict[str, Any]) -> str:
    for key in ["date_month", "pub_month"]:
        value = str(row.get(key) or "")
        if re.match(r"^\d{4}-\d{2}$", value):
            return value
    for key in ["pub_date", "use_date", "IssueDate"]:
        text = str(row.get(key) or "")
        m = re.search(r"(19\d{2}|20\d{2})(?:[.\-/年](\d{1,2}))?", text)
        if m:
            month = int(m.group(2) or 1)
            month = month if 1 <= month <= 12 else 1
            return f"{int(m.group(1)):04d}-{month:02d}"
    return ""


def month_range(start: str, end: str) -> List[str]:
    if not start or not end:
        return []
    sy, sm = map(int, start.split("-"))
    ey, em = map(int, end.split("-"))
    out = []
    y, m = sy, sm
    while (y, m) <= (ey, em):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y += 1
            m = 1
    return out


def admin_level(row: Dict[str, Any]) -> str:
    admin = row.get("admin") if isinstance(row.get("admin"), dict) else {}
    level = str(admin.get("level") or "").strip()
    if level:
        return level
    province = str(row.get("province") or "").strip()
    depart = str(row.get("pub_depart") or row.get("IssueDepartment_2") or "")
    if not province or province in {"全国", "国家", "中央"}:
        return "central"
    if re.search(r"国务院|国家|全国|中央|财政部|发改委|文化和旅游部|国家广播电视总局|国家新闻出版署|国家电影局|商务部|工信部|人民银行", depart):
        return "central"
    return "province_or_local"


def row_province(row: Dict[str, Any]) -> str:
    admin = row.get("admin") if isinstance(row.get("admin"), dict) else {}
    province = str(admin.get("province") or row.get("province") or "").strip()
    return province if province in PROVINCES else province


def row_categories(row: Dict[str, Any]) -> List[str]:
    cls = row.get("classification") if isinstance(row.get("classification"), dict) else {}
    candidates = []
    candidates.extend(canonical_list(row.get("culture_industry_categories")))
    candidates.extend(canonical_list(cls.get("target_segments")))
    candidates.extend(canonical_list(row.get("target_segments")))
    out: List[str] = []
    for item in candidates:
        canon = CANONICAL_ALIASES.get(item, item)
        if canon in CULTURE_CATEGORIES and canon not in out:
            out.append(canon)
    if not out:
        out.append("文化产业综合")
    return out


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(round(float(value)))
    except Exception:
        return default


def row_tool_cls(row: Dict[str, Any]) -> Dict[str, Any]:
    return row.get("classification") if isinstance(row.get("classification"), dict) else {}


def add_to_bucket(bucket: Dict[str, Any], row: Dict[str, Any]) -> None:
    cls = row_tool_cls(row)
    bucket["policy_count"] += 1
    bucket["strength_sum"] += safe_int(cls.get("strength_score"))
    bucket["coverage_breadth_sum"] += safe_int(cls.get("coverage_breadth_score"))
    specificity = str(cls.get("measure_specificity") or "unknown")
    tone = str(cls.get("policy_tone") or "unknown")
    timing = str(cls.get("timing") or "unknown")
    side = str(cls.get("policy_side") or "unknown")
    bucket[f"specificity_{specificity}_count"] += 1
    bucket[f"tone_{tone}_count"] += 1
    bucket[f"timing_{timing}_count"] += 1
    bucket[f"side_{side}_count"] += 1
    for tool in canonical_list(cls.get("policy_tools")):
        if tool in TOOL_IDS:
            bucket[f"tool_{tool}_count"] += 1


def empty_bucket() -> Dict[str, Any]:
    keys = {
        "policy_count": 0,
        "strength_sum": 0,
        "coverage_breadth_sum": 0,
    }
    for value in ["guidance_only", "specific_measures", "mixed", "unknown", "uncertain"]:
        keys[f"specificity_{value}_count"] = 0
    for value in ["support", "restrict", "mixed", "neutral", "unknown", "uncertain"]:
        keys[f"tone_{value}_count"] = 0
    for value in ["ex_ante", "ex_post", "mixed", "unknown", "uncertain"]:
        keys[f"timing_{value}_count"] = 0
    for value in ["supply", "demand", "both", "ecosystem", "unknown", "uncertain"]:
        keys[f"side_{value}_count"] = 0
    for tool in TOOL_IDS:
        keys[f"tool_{tool}_count"] = 0
    return keys


def finalize_bucket(bucket: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(bucket)
    count = out.get("policy_count", 0)
    out["strength_mean"] = round(out["strength_sum"] / count, 4) if count else 0
    out["coverage_breadth_mean"] = round(out["coverage_breadth_sum"] / count, 4) if count else 0
    return out


def write_csv(path: Path, rows: Sequence[Dict[str, Any]], fieldnames: Sequence[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        keys = []
        seen = set()
        for row in rows:
            for key in row:
                if key not in seen:
                    keys.append(key)
                    seen.add(key)
        fieldnames = keys
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(fieldnames), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def document_row(row: Dict[str, Any], category: str) -> Dict[str, Any]:
    cls = row_tool_cls(row)
    return {
        "id": row.get("id"),
        "title": row.get("title", ""),
        "province": row_province(row),
        "admin_level": admin_level(row),
        "date_month": month_from_row(row),
        "culture_category": category,
        "pub_depart": row.get("pub_depart", "") or row.get("IssueDepartment_2", ""),
        "pub_date": row.get("pub_date", ""),
        "measure_specificity": cls.get("measure_specificity", ""),
        "policy_tone": cls.get("policy_tone", ""),
        "timing": cls.get("timing", ""),
        "policy_side": cls.get("policy_side", ""),
        "policy_tools": "；".join(canonical_list(cls.get("policy_tools"))),
        "strength_score": cls.get("strength_score", 0),
        "coverage_breadth_score": cls.get("coverage_breadth_score", 0),
        "tool_confidence": cls.get("tool_confidence", ""),
        "decision_reason": cls.get("decision_reason", ""),
    }


def build(args: argparse.Namespace) -> Dict[str, Any]:
    input_path = Path(args.input)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    docs: List[Dict[str, Any]] = []
    central: Dict[Tuple[str, str], Dict[str, Any]] = defaultdict(empty_bucket)
    province: Dict[Tuple[str, str, str], Dict[str, Any]] = defaultdict(empty_bucket)
    months_seen: List[str] = []
    source_counts = Counter()

    for row in iter_jsonl(input_path):
        month = month_from_row(row)
        if not month:
            continue
        months_seen.append(month)
        level = admin_level(row)
        province_name = row_province(row)
        categories = row_categories(row)
        for category in categories:
            docs.append(document_row(row, category))
            if level == "central":
                add_to_bucket(central[(month, category)], row)
                source_counts["central_category_rows"] += 1
            elif province_name:
                add_to_bucket(province[(province_name, month, category)], row)
                source_counts["province_category_rows"] += 1
            else:
                source_counts["missing_province_rows"] += 1

    if not months_seen:
        raise SystemExit(f"No usable months in {input_path}")
    months = month_range(min(months_seen), max(months_seen))

    central_rows = []
    for month in months:
        for category in CULTURE_CATEGORIES:
            row = {"date_month": month, "culture_category": category}
            row.update(finalize_bucket(central[(month, category)]))
            central_rows.append(row)

    province_rows = []
    for province_name in PROVINCES:
        for month in months:
            for category in CULTURE_CATEGORIES:
                row = {"province": province_name, "date_month": month, "culture_category": category}
                row.update(finalize_bucket(province[(province_name, month, category)]))
                province_rows.append(row)

    province_with_central_rows = []
    for row in province_rows:
        c = finalize_bucket(central[(row["date_month"], row["culture_category"])])
        merged = dict(row)
        for key, value in c.items():
            merged[f"central_{key}"] = value
        merged["total_policy_count_including_central"] = row["policy_count"] + c["policy_count"]
        merged["total_strength_sum_including_central"] = row["strength_sum"] + c["strength_sum"]
        merged["total_coverage_breadth_sum_including_central"] = row["coverage_breadth_sum"] + c["coverage_breadth_sum"]
        province_with_central_rows.append(merged)

    doc_path = output_dir / args.documents_csv
    central_path = output_dir / args.central_panel_csv
    province_path = output_dir / args.province_panel_csv
    with_central_path = output_dir / args.province_with_central_csv
    summary_path = output_dir / args.summary_json

    write_csv(doc_path, docs)
    write_csv(central_path, central_rows)
    write_csv(province_path, province_rows)
    write_csv(with_central_path, province_with_central_rows)

    summary = {
        "input": str(input_path),
        "documents_category_rows": len(docs),
        "unique_documents": len({str(row.get("id")) for row in docs}),
        "start_month": min(months_seen),
        "end_month": max(months_seen),
        "months": len(months),
        "province_units": len(PROVINCES),
        "culture_categories": CULTURE_CATEGORIES,
        "source_counts": dict(source_counts),
        "outputs": {
            "documents_csv": str(doc_path),
            "central_panel_csv": str(central_path),
            "province_panel_csv": str(province_path),
            "province_with_central_csv": str(with_central_path),
        },
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--documents-csv", default="culture_industry_policy_documents_by_category.csv")
    parser.add_argument("--central-panel-csv", default="culture_industry_policy_central_category_month_panel.csv")
    parser.add_argument("--province-panel-csv", default="culture_industry_policy_province_category_month_panel.csv")
    parser.add_argument("--province-with-central-csv", default="culture_industry_policy_province_category_month_panel_with_central.csv")
    parser.add_argument("--summary-json", default="culture_industry_policy_province_category_summary.json")
    build(parser.parse_args())


if __name__ == "__main__":
    main()
