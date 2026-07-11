#!/usr/bin/env python3
"""Screen culture-industry policy candidate packages.

This is the no-LLM BeautifulSoup/keyword stage for the culture-industry
workflow. It keeps the screening auditable and intentionally separates two
ideas:

1. culture-industry relatedness, using the NBS 2018 culture and related
   industries categories as the taxonomy; and
2. industrial-policy judgment, which is left to the LLM workflow.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import screen_related_policy_packages as base  # noqa: E402


CULTURE_CATEGORY_TERMS: Dict[str, Sequence[str]] = {
    "新闻信息服务": [
        "新闻信息服务",
        "新闻服务",
        "新闻业",
        "新闻出版",
        "融媒体",
        "县级融媒体",
        "互联网新闻信息",
        "网络信息内容",
    ],
    "内容创作生产": [
        "内容创作生产",
        "文化内容",
        "内容产业",
        "数字内容",
        "出版产业",
        "数字出版",
        "网络出版",
        "网络文学",
        "版权产业",
        "版权贸易",
        "广播电视产业",
        "广电产业",
        "影视产业",
        "电影产业",
        "电视剧产业",
        "纪录片产业",
        "网络视听",
        "动漫产业",
        "游戏产业",
        "演艺产业",
        "艺术品产业",
        "工艺美术产业",
        "非遗工坊",
        "非遗产业",
    ],
    "创意设计服务": [
        "创意设计服务",
        "创意设计",
        "文化创意设计",
        "文创设计",
        "广告产业",
        "广告业",
        "设计服务",
        "工业设计",
        "时尚设计",
        "数字创意",
    ],
    "文化传播渠道": [
        "文化传播渠道",
        "出版发行",
        "发行放映",
        "电影院线",
        "影院",
        "实体书店",
        "广播电视传输",
        "广电网络",
        "网络视听平台",
        "文化电商",
        "文化产品交易",
        "版权交易",
    ],
    "文化投资运营": [
        "文化投资运营",
        "文化产业投资",
        "文化产业基金",
        "文化产业专项资金",
        "文化产业发展专项资金",
        "文化金融",
        "文化资产",
        "文化资源运营",
        "版权运营",
        "文化产权交易",
        "文化企业融资",
    ],
    "文化娱乐休闲服务": [
        "文化娱乐休闲服务",
        "文化娱乐",
        "文化休闲",
        "娱乐场所",
        "演艺场所",
        "剧院",
        "剧场",
        "旅游演艺",
        "文化旅游消费",
        "文化和旅游消费",
        "文旅消费",
        "剧本娱乐",
        "密室逃脱",
        "电子竞技",
        "电竞产业",
        "游艺娱乐",
        "互联网上网服务营业场所",
    ],
    "文化辅助生产和中介服务": [
        "文化辅助生产",
        "文化中介",
        "文化经纪",
        "演出经纪",
        "版权代理",
        "知识产权服务",
        "文化会展",
        "文化展会",
        "印刷复制",
        "文化设备租赁",
        "舞台搭建",
    ],
    "文化装备生产": [
        "文化装备生产",
        "文化装备",
        "广播电视设备",
        "电影设备",
        "舞台设备",
        "演艺装备",
        "游艺器材",
        "娱乐设备",
        "乐器制造",
        "印刷设备",
        "超高清视频设备",
    ],
    "文化消费终端生产": [
        "文化消费终端生产",
        "文化消费终端",
        "文化终端",
        "智能文化设备",
        "数字文化终端",
        "视听终端",
        "电视机",
        "音响设备",
        "摄影摄像器材",
        "可穿戴文化设备",
    ],
}


CULTURE_TERMS = [
    "文化产业",
    "文化及相关产业",
    "文化相关产业",
    "文化创意产业",
    "文创产业",
    "文化企业",
    "文化市场主体",
    "文化产业园",
    "文化产业园区",
    "文化产业示范园区",
    "文化产业基地",
    "文化产业集聚区",
    "国家文化产业示范基地",
    "国家级文化产业示范园区",
    "文化产业项目",
    "文化产业发展专项资金",
    "文化产业专项资金",
    "文化产业投资",
    "文化产业基金",
    "文化金融",
    "文化科技融合",
    "文化数字化",
    "数字文化产业",
    "文化新业态",
    "文化消费",
    "文化和旅游消费",
    "文化旅游消费",
    "文旅消费",
    "文旅产业",
    "文化旅游产业",
    "旅游演艺",
    "文化贸易",
    "文化出口",
    "对外文化贸易",
    "版权产业",
    "版权贸易",
    "新闻出版产业",
    "出版产业",
    "数字出版",
    "网络文学",
    "广播电视产业",
    "广电产业",
    "影视产业",
    "电影产业",
    "电视剧产业",
    "网络视听",
    "动漫产业",
    "游戏产业",
    "电子竞技产业",
    "电竞产业",
    "演艺产业",
    "艺术品产业",
    "工艺美术产业",
    "广告产业",
    "创意设计产业",
    "文化装备",
    "文化装备产业",
    "文化消费终端",
    "实体书店",
    "电影院线",
    "剧本娱乐",
    "非遗工坊",
    "非遗产业",
]

CULTURE_WEAK_TERMS = [
    "文化",
    "文旅",
    "文创",
    "影视",
    "电影",
    "出版",
    "版权",
    "动漫",
    "游戏",
    "电竞",
    "广告",
    "演艺",
    "剧院",
    "书店",
    "艺术品",
    "工艺美术",
    "非遗",
    "广电",
    "网络视听",
    "数字内容",
    "文化装备",
]

INDUSTRY_CONTEXT = (
    "产业|企业|市场主体|市场|消费|贸易|出口|金融|融资|基金|投资|园区|基地|集聚|集群|"
    "示范区|项目|平台|科技|数字化|数字|新业态|发展|扶持|支持|补贴|奖励|专项资金|"
    "品牌|产品|服务|供给|需求|招商|培育|壮大|转型升级|高质量发展"
)

CULTURE_WEAK_PATTERNS: Sequence[Tuple[str, str]] = [
    ("文化+产业政策语境", rf"文化.{{0,50}}({INDUSTRY_CONTEXT})|({INDUSTRY_CONTEXT}).{{0,50}}文化"),
    ("文旅/文创+产业政策语境", rf"(文旅|文创).{{0,60}}({INDUSTRY_CONTEXT})|({INDUSTRY_CONTEXT}).{{0,60}}(文旅|文创)"),
    ("影视电影+产业语境", r"(影视|电影|电视剧|网络视听).{0,60}(产业|企业|项目|基地|园区|发行|放映|拍摄|制作|补贴|扶持|专项资金)|(?:产业|企业|项目|基地|园区|发行|放映|拍摄|制作|补贴|扶持|专项资金).{0,60}(影视|电影|电视剧|网络视听)"),
    ("出版版权+产业语境", r"(出版|数字出版|网络文学|版权|版权贸易).{0,60}(产业|企业|项目|基地|园区|交易|运营|保护|补贴|扶持|专项资金)|(?:产业|企业|项目|基地|园区|交易|运营|保护|补贴|扶持|专项资金).{0,60}(出版|数字出版|网络文学|版权|版权贸易)"),
    ("动漫游戏电竞+产业语境", r"(动漫|游戏|电竞|电子竞技).{0,60}(产业|企业|项目|基地|园区|赛事|消费|补贴|扶持|专项资金)|(?:产业|企业|项目|基地|园区|赛事|消费|补贴|扶持|专项资金).{0,60}(动漫|游戏|电竞|电子竞技)"),
    ("演艺娱乐+产业语境", r"(演艺|剧院|剧场|娱乐场所|剧本娱乐|密室逃脱).{0,60}(产业|企业|项目|消费|运营|补贴|扶持|专项资金|高质量发展)|(?:产业|企业|项目|消费|运营|补贴|扶持|专项资金|高质量发展).{0,60}(演艺|剧院|剧场|娱乐场所|剧本娱乐|密室逃脱)"),
    ("广告设计+产业语境", r"(广告|创意设计|数字创意|工业设计).{0,60}(产业|企业|项目|园区|基地|服务|补贴|扶持|专项资金)|(?:产业|企业|项目|园区|基地|服务|补贴|扶持|专项资金).{0,60}(广告|创意设计|数字创意|工业设计)"),
    ("文化装备+产业语境", r"(文化装备|演艺装备|舞台设备|广播电视设备|电影设备|游艺器材).{0,60}(产业|企业|制造|更新|项目|研发|推广|补贴|扶持)|(?:产业|企业|制造|更新|项目|研发|推广|补贴|扶持).{0,60}(文化装备|演艺装备|舞台设备|广播电视设备|电影设备|游艺器材)"),
    ("文化贸易/消费/金融", r"(文化贸易|对外文化贸易|文化出口|文化消费|文化金融|文化产业基金|文化企业融资|文化产业投资).{0,80}(政策|措施|办法|方案|规划|支持|扶持|补贴|奖励|发展)"),
]

CULTURE_FALSE_POSITIVE_HINTS = base.FALSE_POSITIVE_HINTS + [
    "提案答复",
    "建议答复",
    "答复意见",
    "代表建议",
    "政协提案",
    "精神文明",
    "文明城市",
    "校园文化",
    "廉政文化",
    "安全文化",
    "法治文化",
    "机关文化",
    "企业文化",
    "红色文化教育",
    "公共文化服务",
    "公共文化服务体系",
    "基本公共文化服务",
    "文化馆",
    "图书馆",
    "博物馆",
    "美术馆",
    "文物保护",
    "文化遗产保护",
    "非物质文化遗产保护",
    "群众文化",
    "全民阅读",
    "送戏下乡",
    "农家书屋",
    "文化志愿",
    "文化市场综合执法",
    "导游资格",
    "A级旅游景区",
    "星级饭店",
]

DOMAIN_CONFIGS = [
    base.DomainConfig(
        key="culture_industry",
        label="文化产业相关政策候选",
        terms=CULTURE_TERMS,
        weak_terms=CULTURE_WEAK_TERMS,
        weak_patterns=CULTURE_WEAK_PATTERNS,
        false_positive_hints=CULTURE_FALSE_POSITIVE_HINTS,
        min_score=8,
    )
]


def detect_culture_categories(text: str) -> List[str]:
    hits: List[str] = []
    for category, terms in CULTURE_CATEGORY_TERMS.items():
        if any(term in text for term in terms):
            hits.append(category)
    if "文化产业" in text or "文化及相关产业" in text or "文化相关产业" in text:
        # A broad culture-industry policy can cover the full taxonomy. Keep the
        # label broad rather than forcing all nine categories from one generic hit.
        if not hits:
            hits.append("文化产业综合")
    return hits


def core_or_related(categories: Sequence[str]) -> str:
    core = {
        "新闻信息服务",
        "内容创作生产",
        "创意设计服务",
        "文化传播渠道",
        "文化投资运营",
        "文化娱乐休闲服务",
    }
    related = {"文化辅助生产和中介服务", "文化装备生产", "文化消费终端生产"}
    seen = set(categories)
    has_core = bool(seen & core or "文化产业综合" in seen)
    has_related = bool(seen & related)
    if has_core and has_related:
        return "核心领域+相关领域"
    if has_core:
        return "文化核心领域"
    if has_related:
        return "文化相关领域"
    return "未细分"


def culture_related_text(record: Dict[str, Any], cleaned_text: str, scan_chars: int) -> str:
    return "\n".join(
        [
            str(record.get("title") or ""),
            str(record.get("pub_depart") or ""),
            str(record.get("IssueDepartment_2") or ""),
            cleaned_text[:scan_chars],
        ]
    )


def reply_like_title(title: str) -> bool:
    return bool(
        re.search(
            r"(人大|政协|代表建议|委员提案|建议第|提案第|提案办理|答复|复函|会办意见|答复意见)",
            title,
        )
    )


class CulturePackageWriter:
    def __init__(self, config: base.DomainConfig, root: Path, input_path: Path) -> None:
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
            "culture_industry_categories",
            "culture_core_or_related",
            "keyword_hits",
            "policy_keyword_hits",
            "false_positive_hints",
            "text_char_len",
            "detail_url",
            "txt_path",
        ]
        self.csv_writer = csv.DictWriter(self.csv_fh, fieldnames=self.csv_fields)
        self.csv_writer.writeheader()
        self.readme_path.write_text(self.package_readme(input_path), encoding="utf-8")
        self.count = 0

    def package_readme(self, input_path: Path) -> str:
        cats = "\n".join(f"- {name}: {'；'.join(terms)}" for name, terms in CULTURE_CATEGORY_TERMS.items())
        return f"""# {self.config.label}

本文件夹由 `scripts/screen_culture_industry_policy_packages.py` 生成。

输入文件：`{input_path}`

本包只做文化产业相关候选筛选，不做 LLM 产业政策判断，不做政策工具分类。

## 类型口径

采用国家统计局《文化及相关产业分类（2018）》九大类作为可复用的类型框架：

{cats}

## 筛选口径

候选需要命中文化产业强关键词，或文化/文旅/文创/影视/出版/版权/动漫/游戏/演艺/广告/设计/文化装备等弱词与产业、企业、市场、消费、金融、园区、基地、项目、扶持、补贴等政策/产业语境共同出现，并达到候选分数阈值 `{self.config.min_score}`。

该规则是偏高召回的候选包；最终是否构成产业政策由后续 LLM 按 Decoding China's Industrial Policies 的窄口径定义判断。
"""

    def close(self) -> None:
        self.jsonl_fh.close()
        self.csv_fh.close()

    def write(
        self,
        record: Dict[str, Any],
        text: str,
        score: int,
        hits: List[str],
        policy_hits: List[str],
        fp: List[str],
        max_body_chars: int,
        scan_chars: int,
    ) -> None:
        self.count += 1
        month, precision = base.parse_month(record.get("pub_date"), record.get("use_date"), record.get("IssueDate"))
        snippet = base.keyword_windows(text, hits or self.config.terms, max_chars=max_body_chars)
        category_text = culture_related_text(record, text, scan_chars)
        categories = detect_culture_categories(category_text)
        block_start = ((self.count - 1) // 1000) * 1000 + 1
        block_end = block_start + 999
        block_dir = self.txt_root / f"{block_start:06d}-{block_end:06d}"
        block_dir.mkdir(parents=True, exist_ok=True)
        txt_path = block_dir / f"{self.count:06d}_id{base.safe_name(record.get('id'), 24)}_{base.safe_name(record.get('title'), 70)}.txt"

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
            "culture_industry_categories": categories,
            "culture_core_or_related": core_or_related(categories),
            "keyword_hits": hits,
            "culture_industry_keyword_hits": hits,
            "policy_keyword_hits": policy_hits,
            "false_positive_hints": fp,
            "text_char_len": len(text),
            "txt_path": str(txt_path),
            "body_snippet": snippet,
        }
        self.jsonl_fh.write(json.dumps(item, ensure_ascii=False) + "\n")
        self.jsonl_fh.flush()
        self.csv_writer.writerow({field: self.csv_value(item.get(field, "")) for field in self.csv_fields})
        self.csv_fh.flush()
        txt_path.write_text(self.txt_body(item), encoding="utf-8")

    @staticmethod
    def csv_value(value: Any) -> str:
        if isinstance(value, list):
            value = "；".join(str(v) for v in value)
        return base.norm_space(str(value or "").replace("\r", " ").replace("\n", " "))

    @staticmethod
    def txt_body(item: Dict[str, Any]) -> str:
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
            f"culture_industry_categories: {'；'.join(item.get('culture_industry_categories') or [])}",
            f"culture_core_or_related: {item.get('culture_core_or_related')}",
            f"keyword_hits: {'；'.join(item.get('keyword_hits') or [])}",
            f"policy_keyword_hits: {'；'.join(item.get('policy_keyword_hits') or [])}",
            f"false_positive_hints: {'；'.join(item.get('false_positive_hints') or [])}",
            f"detail_url: {item.get('detail_url')}",
            "",
            "=== 正文片段 ===",
            str(item.get("body_snippet") or ""),
        ]
        return "\n".join(lines).rstrip() + "\n"


def print_progress(scanned: int, cleaned: int, writer: CulturePackageWriter, started: float, final: bool = False) -> None:
    elapsed = max(0.1, time.time() - started)
    rate = scanned / elapsed
    tag = "FINAL" if final else "PROGRESS"
    print(
        f"[{tag}] scanned={scanned:,} cleaned={cleaned:,} culture_industry={writer.count:,} "
        f"rate={rate:,.1f}/s elapsed={elapsed/60:.1f}m",
        flush=True,
    )


def run(args: argparse.Namespace) -> Dict[str, Any]:
    input_path = Path(args.input)
    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    config = DOMAIN_CONFIGS[0]
    writer = CulturePackageWriter(config, output_root, input_path)
    stats = {"maybe": 0, "candidates": 0}
    scanned = cleaned = 0
    started = time.time()
    try:
        for record in base.iter_json_records(input_path, args.json_prefix):
            scanned += 1
            if args.max_records and scanned > args.max_records:
                break
            if not base.raw_maybe_domain(record, config, args.raw_prefilter_chars):
                if scanned % args.progress_every == 0:
                    print_progress(scanned, cleaned, writer, started)
                continue
            stats["maybe"] += 1
            text = base.clean_text(record)
            cleaned += 1
            title = str(record.get("title") or "")
            if reply_like_title(title):
                continue
            score, hits, policy_hits, fp = base.score_domain(title, text, config, args.scan_chars)
            if score >= config.min_score:
                writer.write(record, text, score, hits, policy_hits, fp, args.max_body_chars, args.scan_chars)
                stats["candidates"] += 1
            if scanned % args.progress_every == 0:
                print_progress(scanned, cleaned, writer, started)
        print_progress(scanned, cleaned, writer, started, final=True)
    finally:
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
                "candidate_count": writer.count,
                "min_score": config.min_score,
                "terms": list(config.terms),
                "weak_terms": list(config.weak_terms),
                "weak_patterns": [{"label": label, "pattern": pattern} for label, pattern in config.weak_patterns],
                "policy_terms": base.POLICY_TERMS,
                "false_positive_hints": list(config.false_positive_hints),
                "culture_category_terms": {k: list(v) for k, v in CULTURE_CATEGORY_TERMS.items()},
                "folder": str(writer.root),
                **stats,
            }
        },
    }
    (output_root / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    domain_summary = {
        **summary["domains"][config.key],
        "scanned_records": scanned,
        "cleaned_records": cleaned,
        "elapsed_seconds": summary["elapsed_seconds"],
    }
    writer.summary_path.write_text(json.dumps(domain_summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-root", default="outputs/policy_packages_culture")
    parser.add_argument("--json-prefix", default="item")
    parser.add_argument("--scan-chars", type=int, default=20000)
    parser.add_argument("--raw-prefilter-chars", type=int, default=20000)
    parser.add_argument("--max-body-chars", type=int, default=3500)
    parser.add_argument("--progress-every", type=int, default=10000)
    parser.add_argument("--max-records", type=int, default=0)
    run(parser.parse_args())


if __name__ == "__main__":
    main()
