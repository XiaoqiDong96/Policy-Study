#!/usr/bin/env python3
"""Refine policy-tool classifications for confirmed domain industrial policies."""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any, Dict, Iterable, List

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from domain_policy_pipeline import DOMAIN_SPECS  # noqa: E402
from nev_policy_pipeline import TOOL_DEFS, call_ollama, canonical_list, safe_float  # noqa: E402


VALID_SPECIFICITY = {"guidance_only", "specific_measures", "mixed"}
VALID_TONE = {"support", "restrict", "mixed", "neutral"}
VALID_TIMING = {"ex_ante", "ex_post", "mixed"}
VALID_SIDE = {"supply", "demand", "both", "ecosystem"}


TOOL_SYSTEM_PROMPT_TEMPLATE = """你是研究中国{domain_label}产业政策的政策工具编码员。
只做政策工具和政策属性分类，不重新判断该文件是否属于{domain_label}产业政策。
必须严格依据标题和正文证据，输出且只输出一个 JSON 对象。
"""


TOOL_PROMPT_TEMPLATE = """请对以下“已确认属于{domain_label}产业政策”的文件进行具体政策工具分类。

分类原则：
1. 不再重新判断是否为产业政策；默认它已经是{domain_label}产业政策。
2. 如果文件只有正式规划、发展方向、重点任务、重大工程、产业布局、技术路线、资源配置导向，而没有补贴/准入/申报/采购/监管细则，仍然分类，measure_specificity 设为 guidance_only。
3. 只从给定 policy_tools 表中选择工具 id，可多选；如果是导向型政策，选择最贴近的宽口径工具，例如 industrial_promotion、technology_rd_adoption、investment_policy、infrastructure_investment、industrial_cluster。
4. strength_score 表示政策工具强度，0-5 整数；coverage_breadth_score 表示覆盖广度，0-5 整数。
5. timing 表示事前/事后：ex_ante / ex_post / mixed。
6. policy_side 表示供给/需求：supply / demand / both / ecosystem。
7. policy_tone 表示支持/限制：support / restrict / mixed / neutral。

{domain_label}环节可包括：{segment_hints}

policy_tools 固定表：
{tool_table}

必须输出 JSON，字段如下：
{{
  "measure_specificity": "guidance_only|specific_measures|mixed",
  "policy_tone": "support|restrict|mixed|neutral",
  "timing": "ex_ante|ex_post|mixed",
  "policy_side": "supply|demand|both|ecosystem",
  "policy_tools": ["从固定表选择的工具 id"],
  "target_segments": ["围绕{domain_label}产业链/应用/监管环节概括"],
  "specific_measures": ["简短列出具体措施；没有则空数组"],
  "eligibility_conditions": ["补贴/准入/申报/采购/试点/备案等条件；没有则空数组"],
  "implementation_mechanisms": ["牵头部门、资金、试点、考核、申报、目录、标准、监管等执行机制；没有则空数组"],
  "strength_score": 0,
  "coverage_breadth_score": 0,
  "tool_confidence": 0.0,
  "evidence": "不超过120字，引用或概括最关键依据"
}}

文件元数据：
- id: {id}
- 标题: {title}
- 发布部门: {pub_depart}
- 日期: {pub_date}
- 省份: {province}

正文：
{body}
"""


def tool_table() -> str:
    return "\n".join(f"- {tool_id}: {meta['label']}；group={meta['group']}" for tool_id, meta in TOOL_DEFS.items())


def iter_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def done_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {str(obj.get("id")) for obj in iter_jsonl(path)}


def clamp_int(value: Any, lo: int = 0, hi: int = 5) -> int:
    try:
        num = int(round(float(value)))
    except Exception:
        return lo
    return max(lo, min(hi, num))


def norm_choice(value: Any, valid: set[str], default: str) -> str:
    text = str(value or "").strip()
    return text if text in valid else default


def normalize_tool_result(raw: Dict[str, Any]) -> Dict[str, Any]:
    tools = canonical_list(raw.get("policy_tools"), set(TOOL_DEFS))
    groups = sorted({TOOL_DEFS[t]["group"] for t in tools})
    confidence = max(0.0, min(1.0, safe_float(raw.get("tool_confidence"), 0.0)))
    return {
        "measure_specificity": norm_choice(raw.get("measure_specificity"), VALID_SPECIFICITY, "guidance_only"),
        "policy_tone": norm_choice(raw.get("policy_tone"), VALID_TONE, "support"),
        "timing": norm_choice(raw.get("timing"), VALID_TIMING, "ex_ante"),
        "policy_side": norm_choice(raw.get("policy_side"), VALID_SIDE, "ecosystem"),
        "policy_tools": tools,
        "tool_groups": groups,
        "target_segments": canonical_list(raw.get("target_segments"))[:12],
        "specific_measures": canonical_list(raw.get("specific_measures"))[:12],
        "eligibility_conditions": canonical_list(raw.get("eligibility_conditions"))[:12],
        "implementation_mechanisms": canonical_list(raw.get("implementation_mechanisms"))[:12],
        "strength_score": clamp_int(raw.get("strength_score")),
        "coverage_breadth_score": clamp_int(raw.get("coverage_breadth_score")),
        "tool_confidence": round(confidence, 4),
        "evidence": str(raw.get("evidence") or "")[:300],
    }


def domain_related(cls: Dict[str, Any], domain_key: str) -> bool:
    domain_field = f"is_{domain_key}_related"
    if domain_field in cls:
        return bool(cls.get(domain_field))
    if "is_nev_related" in cls:
        return bool(cls.get("is_nev_related"))
    return any(key.startswith("is_") and key.endswith("_related") and bool(value) for key, value in cls.items())


def is_confirmed_policy(row: Dict[str, Any], domain_key: str) -> bool:
    if row.get(f"final_is_{domain_key}_industrial_policy") is True:
        return True
    if row.get("dual_vote_final") == "yes":
        return True
    cls = row.get("classification") or {}
    return bool(domain_related(cls, domain_key) and cls.get("is_industrial_policy"))


def prompt_for_row(row: Dict[str, Any], args: argparse.Namespace) -> str:
    body = str(row.get("llm_body") or row.get("full_text") or row.get("body") or "")
    if len(body) > args.max_body_chars:
        body = body[: args.max_body_chars] + "\n...[truncated]..."
    return TOOL_PROMPT_TEMPLATE.format(
        domain_label=args.domain_label,
        segment_hints=args.segment_hints,
        tool_table=tool_table(),
        id=row.get("id", ""),
        title=row.get("title", ""),
        pub_depart=row.get("pub_depart", "") or row.get("IssueDepartment_2", ""),
        pub_date=row.get("pub_date", "") or row.get("IssueDate", ""),
        province=row.get("province", ""),
        body=body,
    )


def refine_one(row: Dict[str, Any], args: argparse.Namespace) -> Dict[str, Any]:
    prompt = prompt_for_row(row, args)
    system_prompt = TOOL_SYSTEM_PROMPT_TEMPLATE.format(domain_label=args.domain_label)
    try:
        raw, _ = call_ollama(
            prompt=prompt,
            model=args.model,
            host=args.ollama_host,
            timeout=args.llm_timeout,
            num_ctx=args.num_ctx,
            system_prompt=system_prompt,
            ollama_format=args.ollama_format,
            max_retries=args.llm_retries,
            retry_base_sleep=args.retry_base_sleep,
        )
        tool_cls = normalize_tool_result(raw)
        error = ""
    except Exception as exc:
        tool_cls = normalize_tool_result({})
        error = repr(exc)[:500]
    return {
        "id": row.get("id"),
        "title": row.get("title", ""),
        "pub_date": row.get("pub_date", ""),
        "province": row.get("province", ""),
        "admin": row.get("admin", {}),
        "source_classification": row.get("classification", {}),
        "tool_classification": tool_cls,
        "tool_model": args.model,
        "tool_error": error,
        "domain": args.domain_key,
        "domain_label": args.domain_label,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--domain-key", required=True)
    parser.add_argument("--domain-label", default="")
    parser.add_argument("--segment-hints", default="")
    parser.add_argument("--input-classified", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--model", default="minimax-m2.5:cloud")
    parser.add_argument("--ollama-host", default="http://127.0.0.1:11434")
    parser.add_argument("--ollama-format", default="auto")
    parser.add_argument("--parallel-docs", type=int, default=4)
    parser.add_argument("--max-body-chars", type=int, default=8000)
    parser.add_argument("--num-ctx", type=int, default=16384)
    parser.add_argument("--llm-timeout", type=int, default=600)
    parser.add_argument("--llm-retries", type=int, default=4)
    parser.add_argument("--retry-base-sleep", type=float, default=8.0)
    parser.add_argument("--progress-every", type=int, default=100)
    parser.add_argument("--max-records", type=int, default=0)
    parser.add_argument("--include-unconfirmed", action="store_true")
    args = parser.parse_args()

    if args.domain_key in DOMAIN_SPECS:
        spec = DOMAIN_SPECS[args.domain_key]
        args.domain_label = args.domain_label or spec.label
        args.segment_hints = args.segment_hints or "、".join(spec.segment_hints)
    if not args.domain_label:
        args.domain_label = args.domain_key
    if not args.segment_hints:
        args.segment_hints = args.domain_label

    input_path = Path(args.input_classified)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    completed = done_ids(output_path)

    rows: List[Dict[str, Any]] = []
    for row in iter_jsonl(input_path):
        if str(row.get("id")) in completed:
            continue
        if not args.include_unconfirmed and not is_confirmed_policy(row, args.domain_key):
            continue
        rows.append(row)
        if args.max_records and len(rows) >= args.max_records:
            break

    total_existing = len(completed)
    started = time.time()
    processed = 0
    errors = 0
    with output_path.open("a", encoding="utf-8") as out_fh, ThreadPoolExecutor(max_workers=max(1, args.parallel_docs)) as pool:
        pending = {}
        iterator = iter(rows)
        while True:
            while len(pending) < max(1, args.parallel_docs):
                try:
                    row = next(iterator)
                except StopIteration:
                    break
                pending[pool.submit(refine_one, row, args)] = row.get("id")
            if not pending:
                break
            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            for fut in done:
                pending.pop(fut, None)
                item = fut.result()
                processed += 1
                if item.get("tool_error"):
                    errors += 1
                out_fh.write(json.dumps(item, ensure_ascii=False) + "\n")
                out_fh.flush()
                if processed % max(1, args.progress_every) == 0 or processed == len(rows):
                    elapsed = max(0.1, time.time() - started)
                    rate = processed / elapsed * 60
                    remaining = max(0, len(rows) - processed)
                    eta = remaining / rate * 60 if rate else math.inf
                    print(
                        f"[{args.domain_key} TOOL REFINE] processed={processed:,}/{len(rows):,} "
                        f"existing={total_existing:,} errors={errors:,} "
                        f"rate={rate:.2f}/min eta={eta/60:.1f}m",
                        flush=True,
                    )
    print(f"Output: {output_path}", flush=True)


if __name__ == "__main__":
    main()
