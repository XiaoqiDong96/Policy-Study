#!/usr/bin/env python3
"""Generic domain wrapper around the NEV policy pipeline.

The original NEV pipeline contains the stable IO, Ollama, resume, and panel
machinery. This module patches only the domain dictionary, prompt, calibration,
and defaults for a named industry domain.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import nev_policy_pipeline as base  # noqa: E402
import screen_future_low_altitude_policy_packages as screen_defs  # noqa: E402
import screen_culture_industry_policy_packages as culture_screen_defs  # noqa: E402

ORIGINAL_COMPACT_RECORD = base.compact_record
ORIGINAL_CANDIDATE_FROM_EXISTING = base.candidate_from_existing_candidate
ORIGINAL_CLASSIFY_ONE_CANDIDATE = base.classify_one_candidate


@dataclass(frozen=True)
class DomainSpec:
    key: str
    label: str
    short_label: str
    related_field: str
    terms: Sequence[str]
    weak_terms: Sequence[str]
    weak_patterns: Sequence[Tuple[str, str]]
    false_positive_hints: Sequence[str]
    scope: str
    exclusion: str
    segment_hints: Sequence[str]
    guidance_terms: str
    default_candidates: str
    default_output_dir: str
    default_candidates_name: str
    default_classified_name: str


SCREEN_CONFIGS = {cfg.key: cfg for cfg in screen_defs.base.DOMAIN_CONFIGS}
CULTURE_SCREEN_CONFIGS = {cfg.key: cfg for cfg in culture_screen_defs.DOMAIN_CONFIGS}

DOMAIN_SPECS: Dict[str, DomainSpec] = {
    "future_industries": DomainSpec(
        key="future_industries",
        label="六大未来产业",
        short_label="未来产业",
        related_field="is_future_industries_related",
        terms=SCREEN_CONFIGS["future_industries"].terms,
        weak_terms=SCREEN_CONFIGS["future_industries"].weak_terms,
        weak_patterns=SCREEN_CONFIGS["future_industries"].weak_patterns,
        false_positive_hints=SCREEN_CONFIGS["future_industries"].false_positive_hints,
        scope=(
            "未来制造、未来信息、未来材料、未来能源、未来空间、未来健康六大方向，以及其中明确的"
            "量子科技、6G/未来网络、卫星互联网/商业航天/空天信息、人形机器人/具身智能/脑机接口、"
            "生物制造/合成生物、氢能/核聚变/新型储能、先进材料等前沿产业或新赛道。"
        ),
        exclusion=(
            "一般科技创新、一般高新技术、普通数字经济、普通智能制造、一般新材料/新能源/生物医药政策，"
            "只有在六大未来产业或上述前沿赛道被明确列为直接目标时才纳入；单纯出现“未来三年/未来规划”等时间表述不纳入。"
        ),
        segment_hints=[
            "未来制造",
            "未来信息",
            "未来材料",
            "未来能源",
            "未来空间",
            "未来健康",
            "量子科技",
            "6G/未来网络",
            "商业航天/卫星互联网",
            "人形机器人/具身智能",
            "脑机接口",
            "生物制造/合成生物",
            "氢能/核聚变/新型储能",
            "先进材料",
        ],
        guidance_terms="未来产业、前沿技术、新赛道、重大工程、先导区、应用场景、科技成果转化、产业化",
        default_candidates="outputs/policy_packages_future_lowalt/future_industries/candidates.jsonl",
        default_output_dir="outputs/future_industries_policy_panel",
        default_candidates_name="future_industries_candidates_norm.jsonl",
        default_classified_name="future_industries_stage1_minimax.jsonl",
    ),
    "low_altitude_economy": DomainSpec(
        key="low_altitude_economy",
        label="低空经济",
        short_label="低空经济",
        related_field="is_low_altitude_economy_related",
        terms=SCREEN_CONFIGS["low_altitude_economy"].terms,
        weak_terms=SCREEN_CONFIGS["low_altitude_economy"].weak_terms,
        weak_patterns=SCREEN_CONFIGS["low_altitude_economy"].weak_patterns,
        false_positive_hints=SCREEN_CONFIGS["low_altitude_economy"].false_positive_hints,
        scope=(
            "低空经济、低空空域、低空飞行服务、低空基础设施、通用航空、无人机/无人驾驶航空器、"
            "eVTOL/电动垂直起降航空器、飞行汽车、城市空中交通、低空物流/旅游/应急/交通、起降点/起降场等。"
        ),
        exclusion=(
            "一般民航运输、普通机场管理、航空安全宣传、气象中的低空急流/低空切变、机场净空保护、"
            "执法部门偶然使用无人机的普通行政管理，不自动纳入；只有低空经济产业、低空空域、通航产业、"
            "无人机产业/运营/监管或低空应用场景是直接政策目标时才纳入。"
        ),
        segment_hints=[
            "低空经济综合",
            "低空空域/飞行服务",
            "低空基础设施",
            "通用航空",
            "无人机/无人驾驶航空器",
            "eVTOL/飞行汽车",
            "低空物流",
            "低空旅游",
            "航空应急救援",
            "起降点/起降场",
        ],
        guidance_terms="低空经济、低空空域、通航产业、无人机、eVTOL、飞行汽车、低空基础设施、应用场景",
        default_candidates="outputs/policy_packages_future_lowalt/low_altitude_economy/candidates.jsonl",
        default_output_dir="outputs/low_altitude_policy_panel",
        default_candidates_name="low_altitude_candidates_norm.jsonl",
        default_classified_name="low_altitude_stage1_minimax.jsonl",
    ),
    "culture_industry": DomainSpec(
        key="culture_industry",
        label="文化产业",
        short_label="文化产业",
        related_field="is_culture_industry_related",
        terms=CULTURE_SCREEN_CONFIGS["culture_industry"].terms,
        weak_terms=CULTURE_SCREEN_CONFIGS["culture_industry"].weak_terms,
        weak_patterns=CULTURE_SCREEN_CONFIGS["culture_industry"].weak_patterns,
        false_positive_hints=CULTURE_SCREEN_CONFIGS["culture_industry"].false_positive_hints,
        scope=(
            "国家统计局《文化及相关产业分类（2018）》口径下的文化核心领域和文化相关领域，"
            "包括新闻信息服务、内容创作生产、创意设计服务、文化传播渠道、文化投资运营、"
            "文化娱乐休闲服务、文化辅助生产和中介服务、文化装备生产、文化消费终端生产，"
            "以及明确围绕文化企业、文创/文旅融合、数字文化、影视、出版、版权、动漫、游戏、"
            "演艺、广告、文化装备、文化消费、文化贸易、文化金融、文化产业园区/基地/项目的政策。"
        ),
        exclusion=(
            "公共文化服务、精神文明建设、校园/机关/安全/廉政/法治文化、单纯文物保护或非遗保护、"
            "图书馆/博物馆/文化馆等公共事业管理、群众文化活动、文化市场日常执法、导游/景区/饭店等"
            "一般旅游管理文件，不因出现“文化和旅游局”或“文化”字样自动纳入；只有文件直接针对文化产业、"
            "文化企业、文化产品/服务市场、文化消费、文化贸易、文化科技/数字化、文化装备或九大类中的"
            "经营性文化活动，并包含政策措施或明确产业导向时才纳入。"
        ),
        segment_hints=[
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
        ],
        guidance_terms="文化产业、文化企业、文化消费、文创产业、文旅产业、数字文化、影视、出版、版权、动漫、游戏、演艺、文化装备、文化贸易、文化金融、产业园区",
        default_candidates="outputs/policy_packages_culture/culture_industry/candidates.jsonl",
        default_output_dir="outputs/culture_industry_policy_panel",
        default_candidates_name="culture_industry_candidates_norm.jsonl",
        default_classified_name="culture_industry_stage1_minimax.jsonl",
    ),
}


def current_domain_from_argv(argv: Optional[Sequence[str]] = None) -> str:
    args = list(argv if argv is not None else sys.argv[1:])
    if "--domain-key" in args:
        idx = args.index("--domain-key")
        if idx + 1 < len(args):
            return args[idx + 1]
    return "future_industries"


def direct_pattern(spec: DomainSpec) -> str:
    return "|".join(re.escape(term) for term in spec.terms)


def contains_direct_term(text: str, spec: DomainSpec) -> bool:
    return any(term in text for term in spec.terms)


def first_direct_term(text: str, spec: DomainSpec) -> str:
    for term in spec.terms:
        if term in text:
            return term
    return ""


def related_confidence_field(spec: DomainSpec) -> str:
    return f"confidence_{spec.related_field}"


def contains_valid_target(text: str, spec: DomainSpec) -> bool:
    if contains_direct_term(text, spec):
        return True
    return any(re.search(pattern, text, flags=re.IGNORECASE) for _label, pattern in spec.weak_patterns)


def domain_candidate_score(title: str, text: str, spec: DomainSpec) -> Tuple[int, List[str], List[str]]:
    hay_title = title or ""
    hay = f"{hay_title}\n{text[:20000]}"
    hits = [term for term in spec.terms if term in hay]
    weak_hits = [label for label, pattern in spec.weak_patterns if re.search(pattern, hay, flags=re.IGNORECASE)]
    if not hits and not weak_hits:
        return 0, [], []
    score = 0
    if hits:
        score += 2 + min(4, len(set(hits)))
    if weak_hits:
        score += 1 + min(2, len(set(weak_hits)) // 2)
    policy_hits = [term for term in base.POLICY_TERMS if term in hay]
    if policy_hits:
        score += min(3, len(set(policy_hits)) // 2 + 1)
    if any(term in hay_title for term in spec.terms):
        score += 3
    fp = [term for term in spec.false_positive_hints if term in hay_title[:120] or term in hay[:1500]]
    if fp:
        score -= 1
    keyword_hits = sorted(set(hits + weak_hits + [t for t in spec.weak_terms if t in hay]))
    return score, keyword_hits, sorted(set(policy_hits))


def evidence_terms(spec: DomainSpec) -> List[str]:
    return sorted(
        set(
            list(spec.terms)
            + list(spec.weak_terms)
            + base.POLICY_TERMS
            + [
                "补贴",
                "奖励",
                "奖补",
                "扶持",
                "支持",
                "税收",
                "贷款",
                "融资",
                "基金",
                "采购",
                "场景开放",
                "示范应用",
                "试点",
                "准入",
                "备案",
                "标准",
                "监管",
                "处罚",
                "目录",
                "申报",
                "认定",
                "考核",
                "验收",
                "责任单位",
                "牵头单位",
                "重点任务",
                "重点工程",
                "重大工程",
                "行动计划",
                "发展目标",
                "产业链",
                "产业集群",
                "技术路线",
                "基础设施",
            ]
        )
    )


def paragraph_evidence_score(paragraph: str, terms: Sequence[str], position: int, total: int, spec: DomainSpec) -> int:
    score = 0
    direct_hits = sum(1 for term in terms if term and term in paragraph)
    domain_hits = sum(1 for term in spec.terms if term in paragraph)
    weak_hits = sum(1 for term in spec.weak_terms if term in paragraph)
    policy_hits = sum(1 for term in base.POLICY_TERMS if term in paragraph)
    ev_hits = sum(1 for term in evidence_terms(spec) if term in paragraph)
    score += min(25, direct_hits * 8)
    score += min(30, domain_hits * 10 + weak_hits * 4)
    score += min(18, policy_hits * 3)
    score += min(18, ev_hits * 2)
    if domain_hits and policy_hits:
        score += 35
    if domain_hits and any(term in paragraph for term in ["补贴", "采购", "准入", "备案", "标准", "目录", "重点任务", "重大工程", "基础设施"]):
        score += 20
    if re.search(r"\d+(\.\d+)?\s*(%|万元|亿元|个|项|家|台|年|月|日|公里|架|座)", paragraph):
        score += 8
    if any(term in paragraph for term in ["责任单位", "牵头单位", "完成时限", "申报", "验收", "考核", "监督", "处罚"]):
        score += 10
    if base.is_policy_heading(paragraph):
        score += 8
    if position < 6:
        score += 6 - position
    if total and position >= total - 3:
        score += 3
    return score


def build_policy_evidence_pack(text: str, terms: Sequence[str], max_chars: int, spec: DomainSpec, fulltext_threshold: int = 2500) -> str:
    if not text:
        return ""
    if len(text) <= min(max_chars, fulltext_threshold):
        return text
    if max_chars <= 1200:
        return base.keyword_windows(text, terms, max_chars=max_chars, radius=max(220, max_chars // 8))
    paragraphs = base.split_policy_paragraphs(text)
    total = len(paragraphs)
    selected: Dict[int, Tuple[str, str]] = {}

    def add(idx: int, reason: str) -> None:
        if 0 <= idx < total and paragraphs[idx].strip():
            selected[idx] = (paragraphs[idx], reason)

    for idx in range(min(3, total)):
        add(idx, "文首/标题/总则")
    for idx in range(max(0, total - 1), total):
        add(idx, "结尾/附则")
    for idx, paragraph in enumerate(paragraphs):
        if any(term and term in paragraph for term in terms) or contains_valid_target(paragraph, spec):
            add(idx, f"{spec.short_label}直接目标词")
            if idx > 0 and base.is_policy_heading(paragraphs[idx - 1]):
                add(idx - 1, "相关条款标题")
            if idx + 1 < total and len(paragraphs[idx + 1]) <= 260:
                add(idx + 1, "目标词后续短段")
    scored = [
        (paragraph_evidence_score(paragraph, terms, idx, total, spec), idx, paragraph)
        for idx, paragraph in enumerate(paragraphs)
    ]
    scored.sort(key=lambda item: (-item[0], item[1]))
    for score, idx, paragraph in scored[: min(80, len(scored))]:
        if score < 12:
            break
        reason = "政策工具/数字/责任证据"
        if contains_valid_target(paragraph, spec):
            reason = f"{spec.short_label}目标+政策证据"
        if base.is_policy_heading(paragraph):
            reason = "条款标题"
        add(idx, reason)
        if idx > 0 and base.is_policy_heading(paragraphs[idx - 1]):
            add(idx - 1, "相关条款标题")
    header = (
        f"【确定性全文预处理证据包】原文约{len(text):,}字，共{total:,}段。"
        f"本证据包未使用大模型；按规则保留文首、结尾、条款标题、含{spec.short_label}直接目标词的段落、"
        "含政策工具/数字指标/责任机制/申报考核的高分段落。若下列证据仅为综合政策中的附带罗列，"
        "仍应按直接目标原则判否。"
    )
    chunks = [header]
    used = len(header) + 8
    for idx in sorted(selected):
        paragraph, reason = selected[idx]
        label = f"【段落{idx + 1}/{total}；{reason}】"
        chunk = f"{label}\n{paragraph}"
        add_len = len(chunk) + 8
        if used + add_len > max_chars:
            remaining = max_chars - used - len(label) - 12
            if remaining > 120:
                chunks.append(f"{label}\n{paragraph[:remaining]}...")
            break
        chunks.append(chunk)
        used += add_len
    return "\n\n".join(chunks)[:max_chars]


def guidance_tool_for_text(text: str) -> str:
    if re.search(r"(基础设施|平台|中心|网络|空域|机场|起降|算力|试验区)", text):
        return "infrastructure_investment"
    if re.search(r"(研发|研究开发|技术|创新|攻关|标准|试验|验证)", text):
        return "technology_rd_adoption"
    if re.search(r"(产业集群|集群|基地|园区|先导区)", text):
        return "industrial_cluster"
    if re.search(r"(基金|投融资|融资|资本)", text):
        return "industrial_fund"
    return "industrial_promotion"


def guidance_side_for_text(text: str) -> str:
    if re.search(r"(场景|应用|采购|需求|推广|消费|运营)", text):
        return "demand"
    if re.search(r"(基础设施|平台|标准|治理|监管|空域|试验区)", text):
        return "ecosystem"
    return "supply"


def guidance_segment_for_text(text: str, spec: DomainSpec) -> str:
    for seg in spec.segment_hints:
        if any(part and part in text for part in re.split(r"[/、]", seg)):
            return seg
    return spec.short_label


def strong_guidance_match(text: str, spec: DomainSpec) -> Optional[re.Match[str]]:
    if not contains_valid_target(text, spec):
        return None
    target = direct_pattern(spec)
    markers = (
        r"优先主题|重点方向|重点任务|重大工程|发展目标|重点研究|技术路线|产业布局|"
        r"战略性新兴产业|未来产业|培育壮大|推广应用|示范应用|场景开放|研发|研究开发|"
        r"先导区|试验区|试点|基础设施|标准|监管|产业链"
    )
    for pattern in [rf"({target}).{{0,90}}({markers})", rf"({markers}).{{0,90}}({target})"]:
        m = re.search(pattern, text)
        if m:
            return m
    for _label, pattern in spec.weak_patterns:
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if m and re.search(markers, text[max(0, m.start() - 120) : m.end() + 120]):
            return m
    return None


def reset_to_not_policy(cls: Dict[str, Any], reason: str, spec: DomainSpec) -> Dict[str, Any]:
    out = dict(cls)
    related_conf_key = related_confidence_field(spec)
    out[spec.related_field] = False
    out["is_nev_related"] = False
    out["is_industrial_policy"] = False
    out[related_conf_key] = min(base.safe_float(out.get(related_conf_key)), 0.30)
    out["confidence_is_nev_related"] = out[related_conf_key]
    out["confidence_is_industrial_policy"] = min(base.safe_float(out.get("confidence_is_industrial_policy")), 0.30)
    out["classification_confidence"] = max(base.safe_float(out.get("classification_confidence")), 0.85)
    out["false_positive_risk"] = "high"
    out["adversarial_not_policy_case"] = reason[:300]
    out["decision_reason"] = f"结论=否；{reason}"[:500]
    out["policy_tone"] = "uncertain"
    out["timing"] = "uncertain"
    out["policy_side"] = "uncertain"
    out["measure_specificity"] = "uncertain"
    out["policy_tools"] = []
    out["tool_groups"] = []
    out["target_segments"] = []
    out["direct_target_evidence"] = ""
    out["measure_or_guidance_evidence"] = ""
    out["specific_measures"] = []
    out["eligibility_conditions"] = []
    out["implementation_mechanisms"] = []
    out["strength_score"] = 0
    out["coverage_breadth_score"] = 0
    return out


def force_guidance_policy(cls: Dict[str, Any], evidence: str, context: str, spec: DomainSpec) -> Dict[str, Any]:
    out = dict(cls)
    tool = guidance_tool_for_text(context)
    related_conf_key = related_confidence_field(spec)
    out[spec.related_field] = True
    out["is_nev_related"] = True
    out["is_industrial_policy"] = True
    out[related_conf_key] = max(base.safe_float(out.get(related_conf_key, out.get("confidence_is_nev_related"))), 0.78)
    out["confidence_is_nev_related"] = out[related_conf_key]
    out["confidence_is_industrial_policy"] = max(base.safe_float(out.get("confidence_is_industrial_policy")), 0.68)
    out["classification_confidence"] = max(base.safe_float(out.get("classification_confidence")), 0.68)
    out["false_positive_risk"] = "medium"
    out["adversarial_not_policy_case"] = out.get("adversarial_not_policy_case") or f"可能只是综合规划中的一个领域，但原文已把{spec.short_label}列为明确任务。"
    out["decision_reason"] = f"结论=是；原文明确把{spec.short_label}或直接产业链列为重点任务/发展方向：{evidence}"[:500]
    out["direct_target_evidence"] = first_direct_term(evidence, spec) or first_direct_term(context, spec)
    out["measure_or_guidance_evidence"] = evidence[:200]
    out["policy_tone"] = "support"
    out["timing"] = "ex_ante"
    out["policy_side"] = guidance_side_for_text(context)
    out["measure_specificity"] = "guidance_only"
    out["policy_tools"] = [tool]
    out["tool_groups"] = sorted({base.TOOL_DEFS[tool]["group"]})
    out["target_segments"] = sorted(set(out.get("target_segments") or []) | {guidance_segment_for_text(context, spec)})
    out["specific_measures"] = out.get("specific_measures") or []
    out["eligibility_conditions"] = out.get("eligibility_conditions") or []
    out["implementation_mechanisms"] = out.get("implementation_mechanisms") or []
    out["strength_score"] = max(base.safe_int(out.get("strength_score"), low=0, high=5), 2)
    out["coverage_breadth_score"] = max(base.safe_int(out.get("coverage_breadth_score"), low=0, high=5), 1)
    return out


def normalize_classification(raw: Dict[str, Any], spec: DomainSpec) -> Dict[str, Any]:
    tools = base.canonical_list(raw.get("policy_tools"), set(base.TOOL_DEFS))
    groups = sorted({base.TOOL_DEFS[t]["group"] for t in tools})
    tone = str(raw.get("policy_tone") or "uncertain").strip()
    timing = str(raw.get("timing") or "uncertain").strip()
    side = str(raw.get("policy_side") or "uncertain").strip()
    measure_specificity = str(raw.get("measure_specificity") or "uncertain").strip()
    if tone not in base.TONES:
        tone = "uncertain"
    if timing not in base.TIMINGS:
        timing = "uncertain"
    if side not in base.SIDES:
        side = "uncertain"
    if measure_specificity not in base.MEASURE_SPECIFICITIES:
        measure_specificity = "uncertain"
    related_conf_key = related_confidence_field(spec)
    related = bool(raw.get(spec.related_field, raw.get("is_nev_related", False)))
    conf_related = base.safe_float(raw.get(related_conf_key, raw.get("confidence_is_nev_related")))
    out = {
        "domain": spec.key,
        spec.related_field: related,
        "is_nev_related": related,
        "is_industrial_policy": bool(raw.get("is_industrial_policy", False)),
        related_conf_key: conf_related,
        "confidence_is_nev_related": conf_related,
        "confidence_is_industrial_policy": base.safe_float(raw.get("confidence_is_industrial_policy")),
        "classification_confidence": base.safe_float(raw.get("classification_confidence")),
        "false_positive_risk": str(raw.get("false_positive_risk") or "unknown").strip()[:30],
        "adversarial_not_policy_case": str(raw.get("adversarial_not_policy_case") or "").strip()[:300],
        "decision_reason": str(raw.get("decision_reason") or "").strip()[:500],
        "direct_target_evidence": str(raw.get("direct_target_evidence") or "").strip()[:200],
        "measure_or_guidance_evidence": str(raw.get("measure_or_guidance_evidence") or "").strip()[:200],
        "policy_tone": tone,
        "timing": timing,
        "policy_side": side,
        "measure_specificity": measure_specificity,
        "policy_tools": tools,
        "tool_groups": groups,
        "target_segments": base.canonical_list(raw.get("target_segments")),
        "specific_measures": base.canonical_list(raw.get("specific_measures")),
        "eligibility_conditions": base.canonical_list(raw.get("eligibility_conditions")),
        "implementation_mechanisms": base.canonical_list(raw.get("implementation_mechanisms")),
        "strength_score": base.safe_int(raw.get("strength_score"), low=0, high=5),
        "coverage_breadth_score": base.safe_int(raw.get("coverage_breadth_score"), low=0, high=5),
    }
    if not (out[spec.related_field] and out["is_industrial_policy"]):
        return reset_to_not_policy(out, out.get("decision_reason") or f"未同时满足{spec.short_label}直接目标和产业政策条件。", spec)
    reason = out.get("decision_reason", "")
    explicit_negative = bool(re.search(r"^\s*(结论\s*=\s*否|结论[:：]\s*否)", reason, flags=re.IGNORECASE))
    negative = bool(
        re.search(
            r"(结论\s*=\s*否|结论[:：]\s*否|判\s*false|最终判定为否|不属于.*产业政策|不是.*产业政策|"
            r"不符合.*窄口径|不符合.*四个条件|不能判定为.*产业政策)",
            reason,
            flags=re.IGNORECASE,
        )
    )
    rule_violation = bool(
        re.search(
            rf"(没有{spec.short_label}直接目标|缺少{spec.short_label}直接目标|未明确{spec.short_label}直接目标|"
            r"并非直接针对|间接支持|间接受益|间接带动|间接提升|溢出受益|弱相关|"
            r"仅.*提到|只是.*提到|主要.*一般|属于.*一般|不涉及.*资源配置|不涉及.*长期经济结构)",
            reason,
            flags=re.IGNORECASE,
        )
    )
    guidance_positive = bool(
        contains_valid_target(reason, spec)
        and re.search(r"(列为|作为|纳入|明确|提出|设置|确定).{0,45}(优先主题|重点方向|重点任务|重大工程|发展目标|重点研究|技术路线|产业布局|规划方向)", reason)
    )
    if explicit_negative or (negative and not guidance_positive) or rule_violation:
        return reset_to_not_policy(out, reason or f"模型理由显示不符合{spec.short_label}产业政策判定条件。", spec)
    if not contains_valid_target(out.get("direct_target_evidence", "") + "\n" + reason, spec):
        return reset_to_not_policy(out, f"缺少可核验的{spec.short_label}直接目标证据。", spec)
    if guidance_positive and not out["policy_tools"]:
        tool = guidance_tool_for_text(reason)
        out["policy_tools"] = [tool]
        out["tool_groups"] = sorted({base.TOOL_DEFS[tool]["group"]})
        out["measure_specificity"] = "guidance_only"
    return out


def calibrate_with_candidate_context(candidate: Dict[str, Any], cls: Dict[str, Any], spec: DomainSpec) -> Dict[str, Any]:
    title = str(candidate.get("title") or "")
    body = str(candidate.get("llm_body") or "")
    context = "\n".join([title, str(candidate.get("pub_depart") or ""), str(candidate.get("law_type") or ""), body])
    if base.reply_report_or_budget_title(title):
        return reset_to_not_policy(
            cls,
            f"文件标题属于建议/提案答复、报告、统计公报、预算执行或预算决议类文本；即使引用既有{spec.short_label}政策或成绩，也不视为本文件出台产业政策。",
            spec,
        )
    if cls.get(spec.related_field) and cls.get("is_industrial_policy") and not contains_valid_target(context, spec):
        return reset_to_not_policy(cls, f"正文缺少{spec.short_label}直接目标；一般综合政策或间接受益不纳入。", spec)
    if base.formal_policy_title(title):
        m = strong_guidance_match(context, spec)
        if m:
            return force_guidance_policy(cls, m.group(0), context, spec)
    return cls


def compact_record(record: Dict[str, Any], text: str, score: int, hits: List[str], policy_hits: List[str], max_body_chars: int, long_doc_mode: str, spec: DomainSpec) -> Dict[str, Any]:
    out = ORIGINAL_COMPACT_RECORD(record, text, score, hits, policy_hits, max_body_chars, long_doc_mode)
    out["domain"] = spec.key
    out["domain_label"] = f"{spec.label}相关政策候选"
    out["keyword_hits"] = hits
    out[f"{spec.key}_keyword_hits"] = hits
    return out


def candidate_from_existing_candidate(row: Dict[str, Any], args: argparse.Namespace, spec: DomainSpec) -> Dict[str, Any]:
    out = ORIGINAL_CANDIDATE_FROM_EXISTING(row, args)
    hits = base.canonical_list(row.get(f"{spec.key}_keyword_hits") or row.get("keyword_hits") or row.get("nev_keyword_hits"))
    out["domain"] = spec.key
    out["domain_label"] = f"{spec.label}相关政策候选"
    out["keyword_hits"] = hits
    out["nev_keyword_hits"] = hits
    out[f"{spec.key}_keyword_hits"] = hits
    return out


def add_aliases(row: Dict[str, Any], spec: DomainSpec) -> Dict[str, Any]:
    cls = row.get("classification") or {}
    related_conf_key = related_confidence_field(spec)
    if isinstance(cls, dict):
        cls["domain"] = spec.key
        cls[spec.related_field] = bool(cls.get(spec.related_field, cls.get("is_nev_related", False)))
        cls[related_conf_key] = base.safe_float(cls.get(related_conf_key, cls.get("confidence_is_nev_related")))
        cls["is_nev_related"] = cls[spec.related_field]
        cls["confidence_is_nev_related"] = cls[related_conf_key]
    for result in row.get("model_classifications") or []:
        sub_cls = result.get("classification") or {}
        if isinstance(sub_cls, dict):
            sub_cls["domain"] = spec.key
            sub_cls[spec.related_field] = bool(sub_cls.get(spec.related_field, sub_cls.get("is_nev_related", False)))
            sub_cls[related_conf_key] = base.safe_float(sub_cls.get(related_conf_key, sub_cls.get("confidence_is_nev_related")))
            sub_cls["is_nev_related"] = sub_cls[spec.related_field]
            sub_cls["confidence_is_nev_related"] = sub_cls[related_conf_key]
    row["domain"] = spec.key
    return row


def build_system_prompt(spec: DomainSpec) -> str:
    return f"""你是严谨的中国产业政策研究助理。请根据给定政府文件的标题、元数据和正文片段进行结构化编码。

本任务采用 Fang, Li, and Lu (2025), Decoding China's Industrial Policies 的窄口径定义。产业政策是政府为了改变长期经济结构，对特定产业或特定经济活动采取的选择性、定向性干预：政府影响不同行业的相对价格，或用其能够影响/控制的资源配置手段，引导资源流向特定产业或活动。

判断是否为产业政策必须同时满足四个条件：
1. 政策主体是政府或政府部门。
2. 文本包含政府政策措施或明确的导向性政策安排。政策措施不仅包括补贴、税收、准入、监管、项目、采购等具体工具，也包括正式规划、指导意见、战略纲要中对特定产业/经济活动的明确优先方向、发展目标、重点任务、工程安排或资源配置导向。
3. 文本直接偏向特定产业或特定经济活动。
4. 政策目标影响长期经济结构或资源配置。

本任务目标产业是“{spec.label}”。直接目标范围包括：{spec.scope}

排除规则：{spec.exclusion}

产业政策不要求一定有补贴标准、申报细则或可操作项目。正式规划、纲要、指导意见、行动计划中，如果明确把{spec.short_label}或其直接产业链列为优先主题、重点方向、重点任务、重大工程、发展目标、技术路线或产业布局，也属于“导向型产业政策”，用 measure_specificity=`guidance_only` 标记。

请把“本文件出台的政策”和“本文件引用/回顾的既有政策”严格区分。人大政协建议答复、预算执行决议、统计公报、年度报告、工作报告、总结材料中，即使提到{spec.short_label}发展成绩或既有政策，通常只是引用或回顾，不代表该文件本身出台产业政策，应判 false。

产业政策可以是支持性、监管性或抑制性。监管标准、备案规则、准入目录、空域/安全/质量监管等，只要直接针对{spec.short_label}并影响资源配置、进入、生产、研发、应用或市场需求，也可构成产业政策。

请进行独立结构化判断：直接按上述四条件和直接目标原则作最终判断。若证据不足、缺少政策措施或明确导向、缺少{spec.short_label}直接目标、或难以从片段确认，必须判 false。只输出 JSON，不要输出解释性正文。"""


def escape_base_format_template(template: str) -> str:
    placeholders = [
        "doc_id",
        "title",
        "province",
        "pub_depart",
        "law_type",
        "pub_date",
        "use_date",
        "category_1",
        "category_2",
        "body",
    ]
    sentinels = {name: f"@@DOMAIN_TEMPLATE_{name.upper()}@@" for name in placeholders}
    for name, sentinel in sentinels.items():
        template = template.replace("{" + name + "}", sentinel)
    template = template.replace("{", "{{").replace("}", "}}")
    for name, sentinel in sentinels.items():
        template = template.replace(sentinel, "{" + name + "}")
    return template


def build_user_prompt(spec: DomainSpec) -> str:
    segments = "、".join(spec.segment_hints)
    template = f"""请分类以下政府文件是否属于“{spec.label}相关产业政策”，并在通过时给出政策工具和维度。

{spec.label}范围包括：{spec.scope}

请严格执行 Fang, Li, and Lu (2025) 的窄口径判定树。除 B 项允许“明确导向性政策安排”外，任一项答案为“否”或“不确定”，最终就必须判为：
{spec.related_field}=false, is_industrial_policy=false, policy_tools=[], strength_score=0, coverage_breadth_score=0。

判定树：
A. 政策主体是否为政府或政府部门？
B. 文本是否包含具体政府政策措施，或正式、明确、直接针对{spec.short_label}的导向性政策安排？
C. 是否直接偏向{spec.short_label}、直接产业链或专属经济活动？
D. 该措施是否意在影响长期经济结构或资源配置，例如相对价格、进入退出、生产研发、融资土地劳动等投入、基础设施、采购需求、供应链或监管约束？

统一排除规则：
- {spec.exclusion}
- 答复人大政协建议/提案、统计公报、年度报告、工作报告、预算执行情况、预算草案、决算、预算决议、工作分工、会议培训竞赛通知、单纯名单/目录公告，除非该文件本身同步发布实施方案/办法/标准/目录/资金申报规则，否则判 false。
- 标准、准入、备案、监管、目录类文件不是自动排除；如果它们直接针对{spec.short_label}并改变市场准入、生产条件、质量安全、补贴资格、采购资格或监管约束，可判为监管性/限制性/混合型产业政策。
- 如果你认为“可能是”，但证据不完整，为了跨模型一致性，请判 false。
- decision_reason 必须以且只能以 `结论=是；` 或 `结论=否；` 开头，并且必须与 {spec.related_field}、is_industrial_policy、policy_tools、strength_score 完全一致。
- 如果是导向型产业政策但没有具体补贴、准入、项目等工具，不要判否；应设置 measure_specificity=`guidance_only`，并在 policy_tools 中选择最贴近的宽口径工具，如 industrial_promotion、technology_rd_adoption、investment_policy、infrastructure_investment 或 industrial_cluster。

文献工具口径固定为 20 个 policy_tools，只能从下列 id 中选择：
credit_finance, tax_incentives, equity_support, fiscal_subsidies,
industrial_fund, promote_entrepreneurship, investment_policy, business_environment,
market_access_regulation, trade_protection, labor_policy, preferential_land_supply,
infrastructure_investment, technology_rd_adoption, environmental_policy,
consumer_subsidy, government_procurement, industrial_promotion,
industrial_cluster, localization_policy。

横向维度：
- policy_tone: support / restrict / mixed / neutral / uncertain。
- timing: ex_ante / ex_post / mixed / uncertain。
- policy_side: supply / demand / both / ecosystem / uncertain。
- measure_specificity: guidance_only / specific_measures / mixed / uncertain。

target_segments 只能围绕以下环节概括：{segments}。

强度 strength_score 取 0-5；覆盖广度 coverage_breadth_score 取 0-5。

置信度字段必须区分两种含义：
- {related_confidence_field(spec)} / confidence_is_industrial_policy 表示“是”的倾向或概率；明确判否时应接近 0，明确判是时应接近 1。
- classification_confidence 表示你对最终“是/否”判定本身的把握；明确判否也应较高，例如 0.75-0.95。

必须返回一个 JSON 对象，字段如下：
{{
  "{spec.related_field}": true,
  "is_industrial_policy": true,
  "{related_confidence_field(spec)}": 0.0,
  "confidence_is_industrial_policy": 0.0,
  "classification_confidence": 0.0,
  "false_positive_risk": "low",
  "adversarial_not_policy_case": "若判否或有误判风险，简述排除/风险理由，20-80字",
  "decision_reason": "结论=是；最终判断理由，20-120字。若否必须写结论=否；...",
  "direct_target_evidence": "原文中证明{spec.short_label}直接目标的短语；若否则为空",
  "measure_or_guidance_evidence": "原文中证明具体措施或导向安排的短语；若否则为空",
  "policy_tone": "support",
  "timing": "ex_ante",
  "policy_side": "supply",
  "measure_specificity": "specific_measures",
  "policy_tools": ["technology_rd_adoption"],
  "target_segments": ["{spec.segment_hints[0]}"],
  "specific_measures": ["简短列出关键措施"],
  "eligibility_conditions": ["如有资格/条件则列出"],
  "implementation_mechanisms": ["如有考核/监督/部门协同则列出"],
  "strength_score": 3,
  "coverage_breadth_score": 2
}}

元数据：
id: {{doc_id}}
title: {{title}}
province_field: {{province}}
pub_depart: {{pub_depart}}
law_type: {{law_type}}
pub_date: {{pub_date}}
use_date: {{use_date}}
category: {{category_1}} / {{category_2}}

正文输入（短文档为全文；长文档为证据保留式压缩摘要）：
{{body}}
"""
    return escape_base_format_template(template)


def patch_base(spec: DomainSpec) -> None:
    base.DOMAIN_LABEL = spec.label
    base.__doc__ = (
        f"Classify and aggregate Chinese {spec.label} policy candidates with resumable "
        "Ollama-backed industrial-policy coding and auditable panel outputs."
    )
    base.NEV_TERMS = list(spec.terms)
    base.WEAK_NEV_TERMS = list(spec.weak_terms)
    base.FALSE_POSITIVE_HINTS = list(spec.false_positive_hints)
    base.LONG_TEXT_EVIDENCE_TERMS = evidence_terms(spec)
    base.SYSTEM_PROMPT = build_system_prompt(spec)
    base.USER_PROMPT_TEMPLATE = build_user_prompt(spec)
    base.STANDARD_SYSTEM_PROMPT = build_system_prompt(spec)
    base.STANDARD_USER_PROMPT_TEMPLATE = build_user_prompt(spec)
    base.BOUNDARY_VOTE_SYSTEM_PROMPT = build_system_prompt(spec)
    base.BOUNDARY_VOTE_USER_PROMPT_TEMPLATE = build_user_prompt(spec)
    base.candidate_score = lambda title, text: domain_candidate_score(title, text, spec)
    base.paragraph_evidence_score = lambda paragraph, terms, position, total: paragraph_evidence_score(paragraph, terms, position, total, spec)
    base.build_policy_evidence_pack = lambda text, terms, max_chars, fulltext_threshold=2500: build_policy_evidence_pack(text, terms, max_chars, spec, fulltext_threshold)
    base.contains_direct_nev_term = lambda text: contains_direct_term(text, spec)
    base.first_direct_nev_term = lambda text: first_direct_term(text, spec)
    base.contains_core_nev_term = lambda text: contains_direct_term(text, spec)
    base.contains_valid_nev_target = lambda text: contains_valid_target(text, spec)
    base.strong_guidance_match = lambda text: strong_guidance_match(text, spec)
    base.guidance_tool_for_text = guidance_tool_for_text
    base.normalize_classification = lambda raw: normalize_classification(raw, spec)
    base.calibrate_with_candidate_context = lambda candidate, cls: calibrate_with_candidate_context(candidate, cls, spec)
    base.compact_record = lambda record, text, score, hits, policy_hits, max_body_chars=4500, long_doc_mode="compress": compact_record(record, text, score, hits, policy_hits, max_body_chars, long_doc_mode, spec)
    base.candidate_from_existing_candidate = lambda row, args: candidate_from_existing_candidate(row, args, spec)

    def classify_one_candidate(candidate: Dict[str, Any], args: argparse.Namespace, models: Sequence[str]) -> Dict[str, Any]:
        return add_aliases(ORIGINAL_CLASSIFY_ONE_CANDIDATE(candidate, args, models), spec)

    base.classify_one_candidate = classify_one_candidate


def ensure_arg(argv: List[str], flag: str, value: str) -> None:
    if flag not in argv:
        argv.extend([flag, value])


def strip_domain_arg(argv: List[str]) -> List[str]:
    out: List[str] = []
    skip = False
    for idx, item in enumerate(argv):
        if skip:
            skip = False
            continue
        if item == "--domain-key":
            skip = True
            continue
        out.append(item)
    return out


def defaults(argv: Optional[Sequence[str]], spec: DomainSpec) -> List[str]:
    args = strip_domain_arg(list(argv if argv is not None else sys.argv[1:]))
    if not args:
        return args
    command = args[0]
    rest = args[1:]
    if command == "classify":
        ensure_arg(rest, "--input", "dummy")
        ensure_arg(rest, "--existing-candidates", spec.default_candidates)
        ensure_arg(rest, "--output-dir", spec.default_output_dir)
        ensure_arg(rest, "--candidates-name", spec.default_candidates_name)
        ensure_arg(rest, "--classified-name", spec.default_classified_name)
        ensure_arg(rest, "--min-candidate-score", "5")
    elif command == "panel":
        ensure_arg(rest, "--classified", f"{spec.default_output_dir}/{spec.default_classified_name}")
        ensure_arg(rest, "--output-dir", spec.default_output_dir)
        ensure_arg(rest, "--documents-csv", f"{spec.key}_policy_documents.csv")
        ensure_arg(rest, "--expanded-csv", f"{spec.key}_policy_expanded_city_month.csv")
        ensure_arg(rest, "--panel-csv", f"{spec.key}_policy_city_month_panel.csv")
        ensure_arg(rest, "--central-panel-csv", f"{spec.key}_policy_central_month_panel.csv")
        ensure_arg(rest, "--province-panel-csv", f"{spec.key}_policy_province_month_panel.csv")
        ensure_arg(rest, "--prefecture-panel-csv", f"{spec.key}_policy_prefecture_month_panel.csv")
        ensure_arg(rest, "--summary-json", f"{spec.key}_policy_summary.json")
    return [command] + rest


def main(domain_key: Optional[str] = None, argv: Optional[Sequence[str]] = None) -> None:
    key = domain_key or current_domain_from_argv(argv)
    if key not in DOMAIN_SPECS:
        raise SystemExit(f"Unknown domain key: {key}. Available: {', '.join(DOMAIN_SPECS)}")
    spec = DOMAIN_SPECS[key]
    patch_base(spec)
    base.main(defaults(argv, spec))


if __name__ == "__main__":
    main(argv=sys.argv[1:])
