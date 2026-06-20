#!/usr/bin/env python3
"""Screen full legal/regulation JSON into NEV and AI related policy packages.

This script does not call LLMs and does not classify industrial-policy tools.
It only performs BeautifulSoup text cleanup plus transparent keyword/policy
context rules, then writes auditable candidate packages for two domains.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, List, Sequence, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOCAL_DEPS = PROJECT_ROOT / ".codex_deps"
if LOCAL_DEPS.exists():
    sys.path.insert(0, str(LOCAL_DEPS))

try:
    import ijson
except Exception as exc:
    raise SystemExit("Missing dependency ijson. Install it into .codex_deps or your environment.") from exc

try:
    from bs4 import BeautifulSoup
except Exception as exc:
    raise SystemExit("Missing dependency beautifulsoup4. Install it into .codex_deps or your environment.") from exc


POLICY_TERMS = [
    "政策",
    "措施",
    "意见",
    "办法",
    "方案",
    "规划",
    "计划",
    "通知",
    "实施",
    "支持",
    "补贴",
    "奖励",
    "扶持",
    "推广",
    "应用",
    "产业",
    "项目",
    "企业",
    "建设",
    "标准",
    "目录",
    "采购",
    "准入",
    "监管",
    "管理",
    "资金",
    "税收",
    "融资",
    "发展",
    "促进",
    "行动",
    "试点",
    "示范",
]

FALSE_POSITIVE_HINTS = [
    "会议通知",
    "培训通知",
    "名单公示",
    "招标公告",
    "行政处罚",
    "事故调查",
    "节能监察",
    "能源管理体系",
]

NEV_TERMS = [
    "新能源汽车",
    "新能源车",
    "新能源乘用车",
    "新能源商用车",
    "新能源客车",
    "新能源公交",
    "电动汽车",
    "纯电动汽车",
    "纯电动",
    "插电式混合动力",
    "插电混合动力",
    "插电式混合",
    "燃料电池汽车",
    "氢燃料电池汽车",
    "氢能汽车",
    "动力电池",
    "车用动力电池",
    "充电桩",
    "充电设施",
    "充换电",
    "换电站",
    "充电基础设施",
    "电动公交",
    "电动出租",
    "电动物流车",
    "智能网联汽车",
]

NEV_WEAK_TERMS = ["新能源", "汽车", "车辆", "电动车", "充电", "换电", "电池"]

AI_TERMS = [
    "人工智能",
    "新一代人工智能",
    "生成式人工智能",
    "生成式AI",
    "通用人工智能",
    "AI产业",
    "AI技术",
    "AI应用",
    "AI治理",
    "AI监管",
    "AI平台",
    "AI模型",
    "AI算力",
    "AI服务",
    "大模型",
    "大语言模型",
    "基础模型",
    "智能体",
    "具身智能",
    "机器学习",
    "深度学习",
    "智能算法",
    "算法模型",
    "算法推荐",
    "智能算力",
    "人工智能算力",
    "智能计算中心",
    "算力中心",
    "智算中心",
    "AI芯片",
    "智能芯片",
    "数据标注",
    "模型训练",
    "自然语言处理",
    "计算机视觉",
    "语音识别",
    "人脸识别",
    "AIGC",
]

AI_WEAK_TERMS = [
    "AI",
    "算法",
    "算力",
    "模型训练",
    "模型推理",
    "数据标注",
    "智能计算",
    "智算",
]


@dataclass(frozen=True)
class DomainConfig:
    key: str
    label: str
    terms: Sequence[str]
    weak_terms: Sequence[str]
    weak_patterns: Sequence[Tuple[str, str]]
    false_positive_hints: Sequence[str]
    min_score: int = 3


DOMAIN_CONFIGS = [
    DomainConfig(
        key="new_energy_vehicle",
        label="新能源汽车相关政策候选",
        terms=NEV_TERMS,
        weak_terms=NEV_WEAK_TERMS,
        weak_patterns=[
            ("新能源+车", r"新能源.{0,40}(汽车|车辆|车)|(?:汽车|车辆|车).{0,40}新能源"),
            ("充换电+车", r"(充电|换电|充换电).{0,50}(汽车|车辆|公交|出租|物流)|(?:汽车|车辆|公交|出租|物流).{0,50}(充电|换电|充换电)"),
            ("电池+车", r"(动力|车用|汽车|车辆|燃料).{0,40}电池|电池.{0,40}(动力|车用|汽车|车辆|燃料)"),
            ("电动车应用", r"电动车.{0,50}(公交|出租|物流|汽车|推广|应用|充电|换电)"),
        ],
        false_positive_hints=FALSE_POSITIVE_HINTS,
    ),
    DomainConfig(
        key="artificial_intelligence",
        label="人工智能相关政策候选",
        terms=AI_TERMS,
        weak_terms=AI_WEAK_TERMS,
        weak_patterns=[
            ("AI+政策技术语境", r"(?<![A-Za-z])AI(?![A-Za-z]).{0,40}(产业|政策|技术|治理|算法|模型|算力|应用|平台|芯片|服务|监管)|(?:产业|政策|技术|治理|算法|模型|算力|应用|平台|芯片|服务|监管).{0,40}(?<![A-Za-z])AI(?![A-Za-z])"),
            ("算法+治理推荐监管", r"算法.{0,50}(治理|推荐|备案|监管|人工智能|AI|智能)|(?:治理|推荐|备案|监管|人工智能|AI|智能).{0,50}算法"),
            ("算力+AI基础设施", r"算力.{0,50}(中心|平台|网络|调度|智能|人工智能|模型|训练)|(?:中心|平台|网络|调度|智能|人工智能|模型|训练).{0,50}算力"),
            ("模型+AI训练推理", r"(大模型|大语言模型|基础模型|模型训练|模型推理|生成式.{0,20}模型|模型.{0,50}(训练|推理|算法|人工智能|生成式|大语言)|(?:训练|推理|算法|人工智能|生成式|大语言).{0,50}模型)"),
            ("数据+标注语料", r"数据.{0,40}(标注|语料).{0,80}(人工智能|算法|模型|训练)|(?:人工智能|算法|模型|训练).{0,80}数据.{0,40}(标注|语料)"),
            ("智能+AI核心词", r"智能.{0,50}(算法|算力|计算中心|芯片|模型|机器人|语音识别|计算机视觉)|(?:算法|算力|计算中心|芯片|模型|机器人|语音识别|计算机视觉).{0,50}智能"),
        ],
        false_positive_hints=FALSE_POSITIVE_HINTS + ["智能水表", "智能电表", "智能快件箱", "智能锁"],
        min_score=5,
    ),
]


def norm_space(text: str) -> str:
    text = text.replace("\u3000", " ").replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def primary_body_source(record: Dict[str, Any]) -> str:
    raw = str(record.get("detail_flag") or "")
    if len(raw.strip()) >= 40:
        return raw
    return str(record.get("detail_html") or "")


def clean_text(record: Dict[str, Any]) -> str:
    source = primary_body_source(record)
    raw = str(record.get("detail_flag") or "")
    if "<" in source and ">" in source:
        try:
            soup = BeautifulSoup(source, "html.parser")
            text = soup.get_text("\n")
        except Exception:
            text = raw
    else:
        text = source
    return norm_space(text or "")


def iter_json_records(path: Path, prefix: str = "item") -> Iterator[Dict[str, Any]]:
    if path.suffix.lower() in {".jsonl", ".ndjson"}:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    yield json.loads(line)
        return
    with path.open("rb") as fh:
        yield from ijson.items(fh, prefix)


def parse_month(*values: Any) -> Tuple[str, str]:
    for value in values:
        s = str(value or "")
        if not s:
            continue
        m = re.search(r"(20\d{2}|19\d{2})(?:[.\-/年](\d{1,2}))?(?:[.\-/月](\d{1,2}))?", s)
        if not m:
            continue
        year = int(m.group(1))
        month = int(m.group(2) or 1)
        if not 1 <= month <= 12:
            month = 1
        precision = "month" if m.group(2) else "year"
        return f"{year:04d}-{month:02d}", precision
    return "", "missing"


def raw_maybe_domain(record: Dict[str, Any], config: DomainConfig, chars: int) -> bool:
    title = str(record.get("title") or "")
    body = primary_body_source(record)
    raw = f"{title}\n{body[:chars]}"
    if any(term in raw for term in config.terms):
        return True
    return any(re.search(pattern, raw, flags=re.IGNORECASE) for _label, pattern in config.weak_patterns)


def score_domain(title: str, text: str, config: DomainConfig, scan_chars: int) -> Tuple[int, List[str], List[str], List[str]]:
    hay_title = title or ""
    hay = f"{hay_title}\n{text[:scan_chars]}"
    hits = [term for term in config.terms if term in hay]
    weak_hits: List[str] = []
    for label, pattern in config.weak_patterns:
        if re.search(pattern, hay, flags=re.IGNORECASE):
            weak_hits.append(label)
    if not hits and not weak_hits:
        return 0, [], [], []

    score = 0
    if hits:
        score += 2 + min(3, len(set(hits)))
    if weak_hits:
        score += 1
    policy_hits = [term for term in POLICY_TERMS if term in hay]
    if policy_hits:
        score += min(3, len(set(policy_hits)) // 2 + 1)
    title_hits = [term for term in config.terms if term in hay_title]
    if title_hits:
        score += 3
    fp = [term for term in config.false_positive_hints if term in hay_title[:120] or term in hay[:1500]]
    if fp:
        score -= 1

    keyword_hits = sorted(set(hits + weak_hits + [t for t in config.weak_terms if t in hay]))
    return score, keyword_hits, sorted(set(policy_hits)), sorted(set(fp))


def keyword_windows(text: str, terms: Sequence[str], max_chars: int = 3500, radius: int = 500) -> str:
    if len(text) <= max_chars:
        return text
    spans: List[Tuple[int, int]] = [(0, min(1000, len(text)))]
    for term in terms:
        start = 0
        while True:
            idx = text.find(term, start)
            if idx < 0:
                break
            spans.append((max(0, idx - radius), min(len(text), idx + len(term) + radius)))
            start = idx + len(term)
            if len(spans) > 16:
                break
        if len(spans) > 16:
            break
    spans.append((max(0, len(text) - 500), len(text)))
    spans = sorted(spans)
    merged: List[Tuple[int, int]] = []
    for start, end in spans:
        if not merged or start > merged[-1][1] + 80:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    chunks: List[str] = []
    used = 0
    for start, end in merged:
        chunk = text[start:end]
        if used + len(chunk) > max_chars:
            chunk = chunk[: max(0, max_chars - used)]
        if chunk:
            chunks.append(chunk)
            used += len(chunk)
        if used >= max_chars:
            break
    return "\n...\n".join(chunks)


def safe_name(value: Any, max_len: int = 80) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[\\/:*?\"<>|\r\n\t]+", "_", text)
    text = re.sub(r"\s+", " ", text)
    return (text[:max_len] or "untitled").strip(" ._")


def package_readme(config: DomainConfig, out_dir: Path, input_path: Path) -> str:
    return f"""# {config.label}

本文件夹由 `scripts/screen_related_policy_packages.py` 生成。

输入文件：`{input_path}`

本包只做相关候选筛选，不做 LLM 产业政策判断，不做政策工具分类。

## 文件说明

- `candidates.jsonl`: 候选全文元数据和正文片段，便于程序继续处理。
- `candidates.csv`: 候选明细表，便于人工浏览。
- `txt/`: 每条候选一个 txt 文件，按每 1000 条分子文件夹保存。
- `summary.json`: 筛选数量、词表、阈值和运行摘要。

## 筛选口径

候选需要命中本领域强关键词或弱词近邻规则，并达到默认候选分数阈值 `{config.min_score}`。

分数由领域关键词、政策语境词、标题命中和误伤扣分共同决定。该规则偏高召回，结果应理解为“相关政策候选包”。
"""


class PackageWriter:
    def __init__(self, config: DomainConfig, root: Path, input_path: Path) -> None:
        self.config = config
        self.root = root / config.key
        self.txt_root = self.root / "txt"
        self.root.mkdir(parents=True, exist_ok=True)
        self.txt_root.mkdir(parents=True, exist_ok=True)
        self.jsonl_path = self.root / "candidates.jsonl"
        self.csv_path = self.root / "candidates.csv"
        self.summary_path = self.root / "summary.json"
        self.readme_path = self.root / "README.md"
        self.jsonl_fh = self.jsonl_path.open("w", encoding="utf-8")
        self.csv_fh = self.csv_path.open("w", encoding="utf-8-sig", newline="")
        self.csv_fields = [
            "seq",
            "id",
            "title",
            "province",
            "pub_depart",
            "law_type",
            "pub_num",
            "pub_date",
            "use_date",
            "date_month",
            "candidate_score",
            "keyword_hits",
            "policy_keyword_hits",
            "false_positive_hints",
            "text_char_len",
            "detail_url",
            "txt_path",
        ]
        self.csv_writer = csv.DictWriter(self.csv_fh, fieldnames=self.csv_fields)
        self.csv_writer.writeheader()
        self.readme_path.write_text(package_readme(config, self.root, input_path), encoding="utf-8")
        self.count = 0

    def close(self) -> None:
        self.jsonl_fh.close()
        self.csv_fh.close()

    def write(self, record: Dict[str, Any], text: str, score: int, hits: List[str], policy_hits: List[str], fp: List[str], max_body_chars: int) -> None:
        self.count += 1
        month, precision = parse_month(record.get("pub_date"), record.get("use_date"), record.get("IssueDate"))
        snippet = keyword_windows(text, hits or self.config.terms, max_chars=max_body_chars)
        block_start = ((self.count - 1) // 1000) * 1000 + 1
        block_end = block_start + 999
        block_dir = self.txt_root / f"{block_start:06d}-{block_end:06d}"
        block_dir.mkdir(parents=True, exist_ok=True)
        txt_path = block_dir / f"{self.count:06d}_id{safe_name(record.get('id'), 24)}_{safe_name(record.get('title'), 70)}.txt"

        item = {
            "seq": self.count,
            "domain": self.config.key,
            "domain_label": self.config.label,
            "id": record.get("id"),
            "law_db_name": record.get("law_db_name", ""),
            "title": record.get("title", ""),
            "detail_url": record.get("detail_url", ""),
            "province": record.get("province", ""),
            "EffectivenessDic": record.get("EffectivenessDic", ""),
            "TimelinessDic": record.get("TimelinessDic", ""),
            "IssueDate": record.get("IssueDate", ""),
            "IssueDepartment_2": record.get("IssueDepartment_2", ""),
            "IssueDepartment_3": record.get("IssueDepartment_3", ""),
            "category_1": record.get("category_1", ""),
            "category_2": record.get("category_2", ""),
            "law_type": record.get("law_type", ""),
            "pub_depart": record.get("pub_depart", ""),
            "pub_num": record.get("pub_num", ""),
            "pub_date": record.get("pub_date", ""),
            "use_date": record.get("use_date", ""),
            "is_time": record.get("is_time", ""),
            "date_month": month,
            "date_precision": precision,
            "candidate_score": score,
            "keyword_hits": hits,
            "policy_keyword_hits": policy_hits,
            "false_positive_hints": fp,
            "text_char_len": len(text),
            "txt_path": str(txt_path),
            "body_snippet": snippet,
        }
        self.jsonl_fh.write(json.dumps(item, ensure_ascii=False) + "\n")
        self.jsonl_fh.flush()

        self.csv_writer.writerow({field: self._csv_value(item.get(field, "")) for field in self.csv_fields})
        self.csv_fh.flush()

        txt_path.write_text(self._txt_body(item), encoding="utf-8")

    @staticmethod
    def _csv_value(value: Any) -> str:
        if isinstance(value, list):
            value = "；".join(str(v) for v in value)
        return norm_space(str(value or "").replace("\r", " ").replace("\n", " "))

    @staticmethod
    def _txt_body(item: Dict[str, Any]) -> str:
        lines = [
            f"seq: {item.get('seq')}",
            f"domain: {item.get('domain_label')}",
            f"id: {item.get('id')}",
            f"title: {item.get('title')}",
            f"province: {item.get('province')}",
            f"pub_depart: {item.get('pub_depart')}",
            f"law_type: {item.get('law_type')}",
            f"pub_num: {item.get('pub_num')}",
            f"pub_date: {item.get('pub_date')}",
            f"use_date: {item.get('use_date')}",
            f"date_month: {item.get('date_month')}",
            f"candidate_score: {item.get('candidate_score')}",
            f"keyword_hits: {'；'.join(item.get('keyword_hits') or [])}",
            f"policy_keyword_hits: {'；'.join(item.get('policy_keyword_hits') or [])}",
            f"false_positive_hints: {'；'.join(item.get('false_positive_hints') or [])}",
            f"detail_url: {item.get('detail_url')}",
            "",
            "=== 正文片段 ===",
            str(item.get("body_snippet") or ""),
        ]
        return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-root", default="outputs/policy_packages")
    parser.add_argument("--json-prefix", default="item")
    parser.add_argument("--scan-chars", type=int, default=20000)
    parser.add_argument("--raw-prefilter-chars", type=int, default=20000)
    parser.add_argument("--max-body-chars", type=int, default=3500)
    parser.add_argument("--progress-every", type=int, default=10000)
    parser.add_argument("--max-records", type=int, default=0)
    args = parser.parse_args()

    input_path = Path(args.input)
    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    writers = {config.key: PackageWriter(config, output_root, input_path) for config in DOMAIN_CONFIGS}
    domain_stats = {config.key: {"maybe": 0, "candidates": 0} for config in DOMAIN_CONFIGS}
    started = time.time()
    scanned = cleaned = 0

    try:
        for record in iter_json_records(input_path, args.json_prefix):
            scanned += 1
            if args.max_records and scanned > args.max_records:
                break

            maybe_configs = [
                config for config in DOMAIN_CONFIGS if raw_maybe_domain(record, config, args.raw_prefilter_chars)
            ]
            if not maybe_configs:
                if scanned % args.progress_every == 0:
                    print_progress(scanned, cleaned, writers, started)
                continue
            for config in maybe_configs:
                domain_stats[config.key]["maybe"] += 1

            text = clean_text(record)
            cleaned += 1
            title = str(record.get("title") or "")

            for config in maybe_configs:
                score, hits, policy_hits, fp = score_domain(title, text, config, args.scan_chars)
                if score < config.min_score:
                    continue
                writers[config.key].write(record, text, score, hits, policy_hits, fp, args.max_body_chars)
                domain_stats[config.key]["candidates"] += 1

            if scanned % args.progress_every == 0:
                print_progress(scanned, cleaned, writers, started)

        print_progress(scanned, cleaned, writers, started, final=True)
    finally:
        for writer in writers.values():
            writer.close()

    summary = {
        "input": str(input_path),
        "output_root": str(output_root),
        "scanned_records": scanned,
        "cleaned_records": cleaned,
        "elapsed_seconds": round(time.time() - started, 2),
        "domains": {
            config.key: {
                "label": config.label,
                "candidate_count": writers[config.key].count,
                "min_score": config.min_score,
                "terms": list(config.terms),
                "weak_terms": list(config.weak_terms),
                "weak_patterns": [{"label": label, "pattern": pattern} for label, pattern in config.weak_patterns],
                "policy_terms": POLICY_TERMS,
                "false_positive_hints": list(config.false_positive_hints),
                "folder": str(writers[config.key].root),
                **domain_stats[config.key],
            }
            for config in DOMAIN_CONFIGS
        },
    }
    (output_root / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    for config in DOMAIN_CONFIGS:
        writer = writers[config.key]
        domain_summary = {
            **summary["domains"][config.key],
            "scanned_records": scanned,
            "cleaned_records": cleaned,
            "elapsed_seconds": summary["elapsed_seconds"],
        }
        writer.summary_path.write_text(json.dumps(domain_summary, ensure_ascii=False, indent=2), encoding="utf-8")


def print_progress(scanned: int, cleaned: int, writers: Dict[str, PackageWriter], started: float, final: bool = False) -> None:
    elapsed = max(0.1, time.time() - started)
    rate = scanned / elapsed
    counts = " ".join(f"{key}={writer.count:,}" for key, writer in writers.items())
    tag = "FINAL" if final else "PROGRESS"
    print(
        f"[{tag}] scanned={scanned:,} cleaned={cleaned:,} {counts} "
        f"rate={rate:,.1f}/s elapsed={elapsed/60:.1f}m",
        flush=True,
    )


if __name__ == "__main__":
    main()
