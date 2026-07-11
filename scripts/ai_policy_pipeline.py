#!/usr/bin/env python3
"""
Classify and aggregate Chinese artificial-intelligence industrial policies.

This is the AI-domain companion to nev_policy_pipeline.py. It reuses the
tested streaming, Ollama, resume, voting, and panel machinery, but replaces the
domain prompt, direct-target rules, keyword scoring, and default paths.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import nev_policy_pipeline as base  # noqa: E402

ORIGINAL_COMPACT_RECORD = base.compact_record
ORIGINAL_CANDIDATE_FROM_EXISTING = base.candidate_from_existing_candidate


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
    "模型推理",
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
    "模型",
    "数据标注",
    "智能计算",
    "智算",
    "智能芯片",
]

AI_WEAK_PATTERNS = [
    ("AI+政策技术语境", r"(?<![A-Za-z])AI(?![A-Za-z]).{0,40}(产业|政策|技术|治理|算法|模型|算力|应用|平台|芯片|服务|监管)|(?:产业|政策|技术|治理|算法|模型|算力|应用|平台|芯片|服务|监管).{0,40}(?<![A-Za-z])AI(?![A-Za-z])"),
    ("算法+治理推荐监管", r"算法.{0,50}(治理|推荐|备案|监管|人工智能|AI|智能|模型)|(?:治理|推荐|备案|监管|人工智能|AI|智能|模型).{0,50}算法"),
    ("算力+AI基础设施", r"算力.{0,50}(中心|平台|网络|调度|智能|人工智能|模型|训练)|(?:中心|平台|网络|调度|智能|人工智能|模型|训练).{0,50}算力"),
    ("模型+AI训练推理", r"(大模型|大语言模型|基础模型|模型训练|模型推理|生成式.{0,20}模型|模型.{0,50}(训练|推理|算法|人工智能|生成式|大语言)|(?:训练|推理|算法|人工智能|生成式|大语言).{0,50}模型)"),
    ("数据+标注语料", r"数据.{0,40}(标注|语料).{0,80}(人工智能|算法|模型|训练)|(?:人工智能|算法|模型|训练).{0,80}数据.{0,40}(标注|语料)"),
    ("智能+AI核心词", r"智能.{0,50}(算法|算力|计算中心|芯片|模型|机器人|语音识别|计算机视觉)|(?:算法|算力|计算中心|芯片|模型|机器人|语音识别|计算机视觉).{0,50}智能"),
]

AI_FALSE_POSITIVE_HINTS = base.FALSE_POSITIVE_HINTS + [
    "智能水表",
    "智能电表",
    "智能快件箱",
    "智能锁",
    "智能停车",
    "智能家居",
]


AI_SYSTEM_PROMPT = """你是严谨的中国产业政策研究助理。请根据给定政府文件的标题、元数据和正文片段进行结构化编码。

本任务采用 Fang, Li, and Lu (2025), Decoding China's Industrial Policies 的窄口径定义。产业政策是政府为了改变长期经济结构，对特定产业或特定经济活动采取的选择性、定向性干预：政府影响不同行业的相对价格，或用其能够影响/控制的资源配置手段，引导资源流向特定产业或活动。

判断是否为产业政策必须同时满足四个条件：
1. 政策主体是政府或政府部门，包括中央、省、市及其所属部门；纯公司、协会、民间主体文本不是产业政策。
2. 文本包含政府政策措施或明确的导向性政策安排。政策措施不仅包括补贴、税收、准入、监管、项目、采购等具体工具，也包括正式规划、指导意见、战略纲要中对特定产业/经济活动的明确优先方向、发展目标、重点任务、工程安排或资源配置导向。
3. 文本直接偏向特定产业或特定经济活动；不针对具体产业/活动的一般政策不是产业政策。
4. 政策目标影响长期经济结构或资源配置；仅应对短期冲击、短期周期波动或临时事务的措施通常不是产业政策。

人工智能目标产业必须是“直接目标”。只有当文件直接针对人工智能产业、人工智能技术研发与应用、大模型/基础模型/生成式人工智能、算法模型、智能算力/智算中心、AI芯片、数据标注与语料、模型训练/推理、人工智能平台服务、人工智能治理监管、具身智能或人工智能产业生态时，才可判为人工智能相关。

一般数字经济、普通信息化、智慧城市、互联网平台、大数据、软件服务、智能制造、机器人、自动化、数据要素或网络安全政策，只有在人工智能或上述直接产业链被明确列为直接目标时才纳入；不能因其可能间接受益人工智能产业而纳入。

产业政策不要求一定有补贴标准、申报细则或可操作项目。正式规划、纲要、指导意见、行动计划中，如果明确把人工智能或其直接产业链列为优先主题、重点方向、重点任务、重大工程、发展目标、技术路线或产业布局，也属于“导向型产业政策”，用 measure_specificity=`guidance_only` 标记。

请把“本文件出台的政策”和“本文件引用/回顾的既有政策”严格区分。人大政协建议答复、预算执行决议、统计公报、年度报告、工作报告、总结材料中，即使提到人工智能产业发展、AI项目落地、智算中心建设或算法治理进展，通常只是引用或回顾，不代表该文件本身出台人工智能产业政策，应判 false。

产业政策可以是支持性、监管性或抑制性。监管标准、备案规则、算法治理、生成式人工智能服务管理、数据安全与模型安全监管等，只要直接针对人工智能产业或其直接产业链并影响资源配置、进入、生产、研发、应用或市场需求，也可构成产业政策。

请使用对抗式判断：先写出“它不是人工智能产业政策”的最强理由，再按上述四条件和直接目标原则作最终判断。若证据不足、缺少具体措施或明确导向、缺少人工智能直接目标、或难以从片段确认，必须判 false。只输出 JSON，不要输出解释性正文。"""


AI_USER_PROMPT_TEMPLATE = """请分类以下政府文件是否属于“人工智能产业政策”，并在通过时给出政策工具和维度。

人工智能范围包括：人工智能产业、人工智能技术研发与应用、大模型/基础模型/生成式人工智能、算法模型、智能算力/智算中心、AI芯片、数据标注与语料、模型训练/推理、人工智能平台服务、人工智能治理监管、具身智能及人工智能产业生态。

请严格执行 Fang, Li, and Lu (2025) 的窄口径判定树。除 B 项允许“明确导向性政策安排”外，任一项答案为“否”或“不确定”，最终就必须判为：
is_ai_related=false, is_industrial_policy=false, policy_tools=[], strength_score=0, coverage_breadth_score=0。

判定树：
A. 政策主体是否为政府或政府部门？
   - 是：继续。
   - 否/不确定：判 false。
B. 文本是否包含具体政府政策措施，或正式、明确、直接针对人工智能产业的导向性政策安排？
   - 是：继续。
   - 否/不确定：判 false。
   - 具体政府政策措施：补贴、税收、融资、基金、采购、准入、监管、标准、项目、示范、平台基础设施、研发支持、土地/劳动/供应链等工具。
   - 导向性政策安排：正式规划/纲要/指导意见中，把人工智能、大模型、算法、智算中心、AI芯片、数据标注、模型训练推理等列为优先产业、重点方向、重点任务、重大工程、发展目标或资源配置方向。
   - 不合格的一般愿景：只说“推进数字经济”“发展新质生产力”“建设智慧城市”“加强科技创新”等泛泛表述，且没有人工智能直接目标。
C. 是否直接偏向人工智能产业、直接产业链或人工智能专属经济活动？
   - 是：继续。
   - 否/不确定：判 false。
   - 注意：“智能”“数字化”“信息化”“大数据”“软件”“互联网”“机器人”“自动化”“智慧城市”泛泛出现，不算直接目标。
D. 该措施是否意在影响长期经济结构或资源配置，例如相对价格、进入退出、生产研发、融资土地劳动等投入、算力/平台基础设施、采购需求、供应链或监管约束？
   - 是：可判 true。
   - 否/不确定：判 false。

统一排除规则：
- 答复人大政协建议/提案、统计公报、年度报告、工作报告、预算执行情况、预算草案、决算、预算决议、工作分工、会议培训竞赛通知、单纯名单/目录公告，除非该文件本身同步发布实施方案/办法/标准/目录/资金申报规则，否则判 false。
- 一般数字经济、普通信息化、智慧城市、普通数据治理、软件服务、互联网平台、网络安全、电子政务、智能制造、机器人或自动化政策，除非人工智能或上述直接产业链是明确直接目标，否则判 false。
- 标准、准入、备案、监管、目录类文件不是自动排除；如果它们直接针对人工智能、大模型、算法服务、生成式人工智能、智能算力、AI芯片、数据标注或模型训练推理，并改变市场准入、生产条件、质量安全、补贴资格、采购资格或监管约束，可判为监管性/限制性/混合型产业政策。
- 国家/地方规划、纲要、指导意见不是自动排除；如果人工智能或其直接产业链是明确直接目标，且文件设置了发展方向、重点任务、重大工程、技术路线、产业布局或资源配置导向，可判为导向型产业政策，measure_specificity=`guidance_only` 或 `mixed`。
- 如果你认为“可能是”，但证据不完整，为了跨模型一致性，请判 false，并把原因写入 adversarial_not_policy_case 和 decision_reason。
- decision_reason 必须以且只能以 `结论=是；` 或 `结论=否；` 开头，并且必须与 is_ai_related、is_industrial_policy、policy_tools、strength_score 完全一致。若结论=否，则两个布尔字段必须都是 false，policy_tools 必须为空，strength_score 和 coverage_breadth_score 必须为 0。
- 严禁输出“结论=是”但理由中又承认“不符合四个条件/不能判定为产业政策/没有人工智能直接目标”。出现这些情况时，必须改为“结论=否”。
- 如果是导向型产业政策但没有具体补贴、准入、项目等工具，不要判否；应设置 measure_specificity=`guidance_only`，并在 policy_tools 中选择最贴近的宽口径工具，如 industrial_promotion、technology_rd_adoption、investment_policy、infrastructure_investment 或 industrial_cluster。

文献工具口径固定为 20 个 policy_tools，只能从下列 id 中选择：
credit_finance, tax_incentives, equity_support, fiscal_subsidies,
industrial_fund, promote_entrepreneurship, investment_policy, business_environment,
market_access_regulation, trade_protection, labor_policy, preferential_land_supply,
infrastructure_investment, technology_rd_adoption, environmental_policy,
consumer_subsidy, government_procurement, industrial_promotion,
industrial_cluster, localization_policy。

横向维度：
- policy_tone: support / restrict / mixed / neutral / uncertain。support 是扶持、促进、补贴、便利化；restrict 是限制、压减、处罚、淘汰或强监管；mixed 同时包含明显扶持和限制。
- timing: ex_ante / ex_post / mixed / uncertain。ex_ante 是准入、规划、标准、预算、建设、事前申报或预防性安排；ex_post 是事后奖惩、绩效评价、清算、追责、检查、处罚、复核；mixed 两者都有。
- policy_side: supply / demand / both / ecosystem / uncertain。supply 面向企业生产、研发、投资、土地、融资、人才、算力、供应链；demand 面向政府采购、场景开放、应用推广、市场需求；ecosystem 面向标准平台、数据语料、智算中心、公共服务和治理环境且供需难分。
- measure_specificity: guidance_only / specific_measures / mixed / uncertain。guidance_only 表示只有正式产业导向、规划目标、重点任务、发展方向或资源配置方向，但没有可操作的申报、补贴、准入、监管、项目、采购等细则。

强度 strength_score 取 0-5：
0=不是政策；1=泛泛表述；2=有方向但措施弱；3=有具体措施或责任；4=有资金、资格、指标、期限、项目或监管机制；5=有明确预算/补贴标准/强制指标/处罚/考核等高约束安排。

覆盖广度 coverage_breadth_score 取 0-5：综合目标环节数量、工具数量、执行主体数量、空间覆盖和产业链覆盖。

置信度字段必须区分两种含义：
- confidence_is_ai_related / confidence_is_industrial_policy 表示“是”的倾向或概率；明确判否时应接近 0，明确判是时应接近 1。
- classification_confidence 表示你对最终“是/否”判定本身的把握；明确判否也应较高，例如 0.75-0.95。只有证据不足、边界案例、可能误判时才低。

必须返回一个 JSON 对象，字段如下：
{{
  "is_ai_related": true,
  "is_industrial_policy": true,
  "confidence_is_ai_related": 0.0,
  "confidence_is_industrial_policy": 0.0,
  "classification_confidence": 0.0,
  "false_positive_risk": "low",
  "adversarial_not_policy_case": "最强反方理由，20-80字",
  "decision_reason": "结论=是；最终判断理由，20-120字。若否必须写结论=否；...",
  "direct_target_evidence": "原文中证明人工智能直接目标的短语；若否则为空",
  "measure_or_guidance_evidence": "原文中证明具体措施或导向安排的短语；若否则为空",
  "policy_tone": "support",
  "timing": "ex_ante",
  "policy_side": "supply",
  "measure_specificity": "specific_measures",
  "policy_tools": ["technology_rd_adoption"],
  "target_segments": ["大模型/算法模型"],
  "specific_measures": ["简短列出关键措施"],
  "eligibility_conditions": ["如有资格/条件则列出"],
  "implementation_mechanisms": ["如有考核/监督/部门协同则列出"],
  "strength_score": 3,
  "coverage_breadth_score": 2
}}

元数据：
id: {doc_id}
title: {title}
province_field: {province}
pub_depart: {pub_depart}
law_type: {law_type}
pub_date: {pub_date}
use_date: {use_date}
category: {category_1} / {category_2}

正文输入（短文档为全文；长文档为证据保留式压缩摘要）：
{body}
"""

AI_STANDARD_SYSTEM_PROMPT = AI_SYSTEM_PROMPT.replace(
    "请使用对抗式判断：先写出“它不是人工智能产业政策”的最强理由，再按上述四条件和直接目标原则作最终判断。",
    "请进行独立结构化判断：直接按上述四条件和直接目标原则作最终判断，不进行多轮对抗、辩论或自我反驳。",
)

AI_STANDARD_USER_PROMPT_TEMPLATE = AI_USER_PROMPT_TEMPLATE.replace(
    'adversarial_not_policy_case": "最强反方理由，20-80字"',
    'adversarial_not_policy_case": "若判否或有误判风险，简述排除/风险理由，20-80字"',
)


def contains_direct_ai_term(text: str) -> bool:
    return any(term in text for term in AI_TERMS)


def first_direct_ai_term(text: str) -> str:
    for term in AI_TERMS:
        if term in text:
            return term
    return ""


def contains_valid_ai_target(text: str) -> bool:
    if contains_direct_ai_term(text):
        return True
    return any(re.search(pattern, text) for _label, pattern in AI_WEAK_PATTERNS)


def ai_candidate_score(title: str, text: str) -> Tuple[int, List[str], List[str]]:
    hay_title = title or ""
    hay = f"{hay_title}\n{text[:20000]}"
    hits = [term for term in AI_TERMS if term in hay]
    weak_hits = [label for label, pattern in AI_WEAK_PATTERNS if re.search(pattern, hay)]
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
    if [term for term in AI_TERMS if term in hay_title]:
        score += 3
    fp = [term for term in AI_FALSE_POSITIVE_HINTS if term in hay_title[:120] or term in hay[:1500]]
    if fp:
        score -= 1
    keyword_hits = sorted(set(hits + weak_hits + [t for t in AI_WEAK_TERMS if t in hay]))
    return score, keyword_hits, sorted(set(policy_hits))


AI_LONG_TEXT_EVIDENCE_TERMS = sorted(
    set(
        AI_TERMS
        + AI_WEAK_TERMS
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
            "语料",
            "数据集",
            "智能算力",
            "智算中心",
        ]
    )
)


def ai_paragraph_evidence_score(paragraph: str, terms: Sequence[str], position: int, total: int) -> int:
    score = 0
    direct_hits = sum(1 for term in terms if term and term in paragraph)
    ai_hits = sum(1 for term in AI_TERMS if term in paragraph)
    weak_hits = sum(1 for term in AI_WEAK_TERMS if term in paragraph)
    policy_hits = sum(1 for term in base.POLICY_TERMS if term in paragraph)
    evidence_hits = sum(1 for term in AI_LONG_TEXT_EVIDENCE_TERMS if term in paragraph)
    score += min(25, direct_hits * 8)
    score += min(30, ai_hits * 10 + weak_hits * 4)
    score += min(18, policy_hits * 3)
    score += min(18, evidence_hits * 2)
    if ai_hits and policy_hits:
        score += 35
    if ai_hits and any(term in paragraph for term in ["补贴", "采购", "准入", "备案", "标准", "目录", "智算中心", "重点任务", "重大工程"]):
        score += 20
    if re.search(r"\d+(\.\d+)?\s*(%|万元|亿元|个|项|家|台|年|月|日|P|算力|tokens?|Token|GPU)", paragraph):
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


def ai_build_policy_evidence_pack(text: str, terms: Sequence[str], max_chars: int, fulltext_threshold: int = 2500) -> str:
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
        has_target = any(term and term in paragraph for term in terms)
        has_ai = any(term in paragraph for term in AI_TERMS + AI_WEAK_TERMS)
        if has_target or has_ai:
            add(idx, "人工智能直接目标词")
            if idx > 0 and base.is_policy_heading(paragraphs[idx - 1]):
                add(idx - 1, "相关条款标题")
            if idx + 1 < total and len(paragraphs[idx + 1]) <= 260:
                add(idx + 1, "目标词后续短段")

    scored = [
        (ai_paragraph_evidence_score(paragraph, terms, idx, total), idx, paragraph)
        for idx, paragraph in enumerate(paragraphs)
    ]
    scored.sort(key=lambda item: (-item[0], item[1]))
    for score, idx, paragraph in scored[: min(80, len(scored))]:
        if score < 12:
            break
        reason = "政策工具/数字/责任证据"
        if any(term in paragraph for term in AI_TERMS + AI_WEAK_TERMS):
            reason = "人工智能目标+政策证据"
        if base.is_policy_heading(paragraph):
            reason = "条款标题"
        add(idx, reason)
        if idx > 0 and base.is_policy_heading(paragraphs[idx - 1]):
            add(idx - 1, "相关条款标题")

    header = (
        f"【确定性全文预处理证据包】原文约{len(text):,}字，共{total:,}段。"
        "本证据包未使用大模型；按规则保留文首、结尾、条款标题、含人工智能直接目标词的段落、"
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


def ai_guidance_tool_for_text(text: str) -> str:
    if re.search(r"(算力|智算|计算中心|平台|语料|数据集|基础设施)", text):
        return "infrastructure_investment"
    if re.search(r"(研发|研究开发|技术|创新|攻关|训练|推理|模型|算法)", text):
        return "technology_rd_adoption"
    if re.search(r"(产业集群|集群|基地|园区)", text):
        return "industrial_cluster"
    return "industrial_promotion"


def ai_guidance_side_for_text(text: str) -> str:
    if re.search(r"(场景|应用|采购|需求|推广)", text):
        return "demand"
    if re.search(r"(算力|智算|平台|语料|标准|治理|监管)", text):
        return "ecosystem"
    return "supply"


def ai_guidance_segment_for_text(text: str) -> str:
    if re.search(r"(大模型|大语言模型|基础模型|生成式)", text):
        return "大模型/生成式人工智能"
    if re.search(r"(算法|推荐算法|算法模型)", text):
        return "算法模型"
    if re.search(r"(算力|智算|计算中心)", text):
        return "智能算力/智算中心"
    if re.search(r"(芯片|AI芯片|智能芯片)", text):
        return "AI芯片"
    if re.search(r"(数据标注|语料|数据集)", text):
        return "数据标注/语料"
    if re.search(r"(治理|监管|备案|安全)", text):
        return "AI治理监管"
    return "人工智能产业"


def ai_strong_guidance_match(text: str) -> Optional[re.Match[str]]:
    if not contains_valid_ai_target(text):
        return None
    target = "|".join(re.escape(term) for term in AI_TERMS)
    markers = (
        r"优先主题|重点方向|重点任务|重大工程|发展目标|重点研究|技术路线|"
        r"产业布局|战略性新兴产业|未来产业|培育壮大|推广应用|示范应用|"
        r"场景开放|算力基础设施|智算中心|研发|研究开发|算法治理|模型训练"
    )
    patterns = [rf"({target}).{{0,80}}({markers})", rf"({markers}).{{0,80}}({target})"]
    for pattern in patterns:
        m = re.search(pattern, text)
        if m:
            return m
    for _label, pattern in AI_WEAK_PATTERNS:
        m = re.search(pattern, text)
        if m and re.search(markers, text[max(0, m.start() - 100) : m.end() + 100]):
            return m
    return None


def ai_reset_to_not_policy(cls: Dict[str, Any], reason: str) -> Dict[str, Any]:
    out = dict(cls)
    out["is_ai_related"] = False
    out["is_nev_related"] = False
    out["is_industrial_policy"] = False
    out["confidence_is_ai_related"] = min(base.safe_float(out.get("confidence_is_ai_related")), 0.30)
    out["confidence_is_nev_related"] = out["confidence_is_ai_related"]
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


def ai_force_guidance_policy(cls: Dict[str, Any], evidence: str, context: str) -> Dict[str, Any]:
    out = dict(cls)
    tool = ai_guidance_tool_for_text(context)
    segment = ai_guidance_segment_for_text(context)
    out["is_ai_related"] = True
    out["is_nev_related"] = True
    out["is_industrial_policy"] = True
    out["confidence_is_ai_related"] = max(base.safe_float(out.get("confidence_is_ai_related")), 0.78)
    out["confidence_is_nev_related"] = out["confidence_is_ai_related"]
    out["confidence_is_industrial_policy"] = max(base.safe_float(out.get("confidence_is_industrial_policy")), 0.68)
    out["classification_confidence"] = max(base.safe_float(out.get("classification_confidence")), 0.68)
    out["false_positive_risk"] = "medium"
    out["adversarial_not_policy_case"] = (
        out.get("adversarial_not_policy_case")
        or "可能只是综合规划中的一个领域，但原文已把人工智能或直接产业链列为明确任务。"
    )
    out["decision_reason"] = f"结论=是；原文明确把人工智能或直接产业链列为重点任务/发展方向：{evidence}"[:500]
    out["direct_target_evidence"] = first_direct_ai_term(evidence) or first_direct_ai_term(context)
    out["measure_or_guidance_evidence"] = evidence[:200]
    out["policy_tone"] = "support"
    out["timing"] = "ex_ante"
    out["policy_side"] = ai_guidance_side_for_text(context)
    out["measure_specificity"] = "guidance_only"
    out["policy_tools"] = [tool]
    out["tool_groups"] = sorted({base.TOOL_DEFS[tool]["group"]})
    out["target_segments"] = sorted(set(out.get("target_segments") or []) | {segment})
    out["specific_measures"] = out.get("specific_measures") or []
    out["eligibility_conditions"] = out.get("eligibility_conditions") or []
    out["implementation_mechanisms"] = out.get("implementation_mechanisms") or []
    out["strength_score"] = max(base.safe_int(out.get("strength_score"), low=0, high=5), 2)
    out["coverage_breadth_score"] = max(base.safe_int(out.get("coverage_breadth_score"), low=0, high=5), 1)
    return out


def ai_normalize_classification(raw: Dict[str, Any]) -> Dict[str, Any]:
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

    ai_related = bool(raw.get("is_ai_related", raw.get("is_nev_related", False)))
    conf_ai = base.safe_float(raw.get("confidence_is_ai_related", raw.get("confidence_is_nev_related")))
    out = {
        "domain": "artificial_intelligence",
        "is_ai_related": ai_related,
        "is_nev_related": ai_related,
        "is_industrial_policy": bool(raw.get("is_industrial_policy", False)),
        "confidence_is_ai_related": conf_ai,
        "confidence_is_nev_related": conf_ai,
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

    if not (out["is_ai_related"] and out["is_industrial_policy"]):
        return ai_reset_to_not_policy(out, out.get("decision_reason") or "未同时满足人工智能直接目标和产业政策条件。")

    reason = out.get("decision_reason", "")
    explicit_negative = bool(re.search(r"^\s*(结论\s*=\s*否|结论[:：]\s*否)", reason, flags=re.IGNORECASE))
    negative = bool(
        re.search(
            r"(结论\s*=\s*否|结论[:：]\s*否|判\s*false|最终判定为否|"
            r"不属于.*产业政策|不是.*产业政策|不符合.*窄口径|不符合.*四个条件|"
            r"不能判定为.*产业政策)",
            reason,
            flags=re.IGNORECASE,
        )
    )
    rule_violation = bool(
        re.search(
            r"(没有人工智能直接目标|缺少人工智能直接目标|未明确人工智能直接目标|并非直接针对人工智能|"
            r"间接支持|间接受益|间接带动|间接提升|溢出受益|弱相关|仅.*提到|只是.*提到|"
            r"主要.*一般|属于.*一般|不涉及.*资源配置|不涉及.*长期经济结构)",
            reason,
            flags=re.IGNORECASE,
        )
    )
    guidance_positive = bool(
        contains_valid_ai_target(reason)
        and re.search(
            r"(列为|作为|纳入|明确|提出|设置|确定).{0,40}"
            r"(优先主题|重点方向|重点任务|重大工程|发展目标|重点研究|技术路线|产业布局|规划方向)",
            reason,
        )
    )
    if explicit_negative or (negative and not guidance_positive) or rule_violation:
        return ai_reset_to_not_policy(out, reason or "模型理由显示不符合人工智能产业政策判定条件。")

    if not contains_valid_ai_target(out.get("direct_target_evidence", "") + "\n" + reason):
        return ai_reset_to_not_policy(out, "缺少可核验的人工智能直接目标证据。")

    out["is_ai_related"] = True
    out["is_nev_related"] = True
    if guidance_positive and not out["policy_tools"]:
        tool = ai_guidance_tool_for_text(reason)
        out["policy_tools"] = [tool]
        out["tool_groups"] = sorted({base.TOOL_DEFS[tool]["group"]})
        out["measure_specificity"] = "guidance_only"
    return out


def ai_calibrate_with_candidate_context(candidate: Dict[str, Any], cls: Dict[str, Any]) -> Dict[str, Any]:
    title = str(candidate.get("title") or "")
    body = str(candidate.get("llm_body") or "")
    context = "\n".join([title, str(candidate.get("pub_depart") or ""), str(candidate.get("law_type") or ""), body])
    if base.reply_report_or_budget_title(title):
        return ai_reset_to_not_policy(
            cls,
            "文件标题属于建议/提案答复、报告、统计公报、预算执行或预算决议类文本；即使引用既有人工智能政策或成绩，也不视为本文件出台产业政策。",
        )
    if cls.get("is_ai_related") and cls.get("is_industrial_policy") and not contains_valid_ai_target(context):
        return ai_reset_to_not_policy(
            cls,
            "正文缺少人工智能、大模型、算法、智能算力、AI芯片、数据标注或模型训练推理等直接目标；一般数字化、信息化、智慧城市或智能制造不纳入人工智能产业政策。",
        )
    if base.formal_policy_title(title):
        m = ai_strong_guidance_match(context)
        if m:
            return ai_force_guidance_policy(cls, m.group(0), context)
    return cls


def ai_compact_record(record: Dict[str, Any], text: str, score: int, hits: List[str], policy_hits: List[str], max_body_chars: int = 4500, long_doc_mode: str = "compress") -> Dict[str, Any]:
    out = ORIGINAL_COMPACT_RECORD(record, text, score, hits, policy_hits, max_body_chars, long_doc_mode)
    out["domain"] = "artificial_intelligence"
    out["domain_label"] = "人工智能相关政策候选"
    out["ai_keyword_hits"] = hits
    out["keyword_hits"] = hits
    return out


def ai_candidate_from_existing_candidate(row: Dict[str, Any], args: argparse.Namespace) -> Dict[str, Any]:
    out = ORIGINAL_CANDIDATE_FROM_EXISTING(row, args)
    hits = base.canonical_list(row.get("ai_keyword_hits") or row.get("keyword_hits") or row.get("nev_keyword_hits"))
    out["domain"] = "artificial_intelligence"
    out["domain_label"] = "人工智能相关政策候选"
    out["ai_keyword_hits"] = hits
    out["keyword_hits"] = hits
    out["nev_keyword_hits"] = hits
    return out


def add_ai_aliases(row: Dict[str, Any]) -> Dict[str, Any]:
    cls = row.get("classification") or {}
    if isinstance(cls, dict):
        cls["domain"] = "artificial_intelligence"
        for key in ["decision_reason", "adversarial_not_policy_case", "direct_target_evidence", "measure_or_guidance_evidence"]:
            if isinstance(cls.get(key), str):
                cls[key] = cls[key].replace("新能源汽车", "人工智能").replace("新能源车", "人工智能")
        cls["is_ai_related"] = bool(cls.get("is_ai_related", cls.get("is_nev_related", False)))
        cls["confidence_is_ai_related"] = base.safe_float(
            cls.get("confidence_is_ai_related", cls.get("confidence_is_nev_related"))
        )
        cls["is_nev_related"] = cls["is_ai_related"]
        cls["confidence_is_nev_related"] = cls["confidence_is_ai_related"]
    for result in row.get("model_classifications") or []:
        sub_cls = result.get("classification") or {}
        if isinstance(sub_cls, dict):
            sub_cls["domain"] = "artificial_intelligence"
            for key in ["decision_reason", "adversarial_not_policy_case", "direct_target_evidence", "measure_or_guidance_evidence"]:
                if isinstance(sub_cls.get(key), str):
                    sub_cls[key] = sub_cls[key].replace("新能源汽车", "人工智能").replace("新能源车", "人工智能")
            sub_cls["is_ai_related"] = bool(sub_cls.get("is_ai_related", sub_cls.get("is_nev_related", False)))
            sub_cls["confidence_is_ai_related"] = base.safe_float(
                sub_cls.get("confidence_is_ai_related", sub_cls.get("confidence_is_nev_related"))
            )
    consensus = row.get("adversarial_consensus") or {}
    if isinstance(consensus, dict):
        if "nev_yes_votes" in consensus:
            consensus["ai_yes_votes"] = consensus["nev_yes_votes"]
            consensus["domain_yes_votes"] = consensus["nev_yes_votes"]
    row["domain"] = "artificial_intelligence"
    if "keyword_hits" not in row and row.get("ai_keyword_hits"):
        row["keyword_hits"] = row.get("ai_keyword_hits")
    return row


def patch_base() -> None:
    base.NEV_TERMS = AI_TERMS
    base.WEAK_NEV_TERMS = AI_WEAK_TERMS
    base.FALSE_POSITIVE_HINTS = AI_FALSE_POSITIVE_HINTS
    base.SYSTEM_PROMPT = AI_SYSTEM_PROMPT
    base.USER_PROMPT_TEMPLATE = AI_USER_PROMPT_TEMPLATE
    base.STANDARD_SYSTEM_PROMPT = AI_STANDARD_SYSTEM_PROMPT
    base.STANDARD_USER_PROMPT_TEMPLATE = AI_STANDARD_USER_PROMPT_TEMPLATE
    base.LONG_TEXT_EVIDENCE_TERMS = AI_LONG_TEXT_EVIDENCE_TERMS

    base.candidate_score = ai_candidate_score
    base.paragraph_evidence_score = ai_paragraph_evidence_score
    base.build_policy_evidence_pack = ai_build_policy_evidence_pack
    base.contains_direct_nev_term = contains_direct_ai_term
    base.first_direct_nev_term = first_direct_ai_term
    base.contains_core_nev_term = contains_direct_ai_term
    base.contains_valid_nev_target = contains_valid_ai_target
    base.strong_guidance_match = ai_strong_guidance_match
    base.guidance_tool_for_text = ai_guidance_tool_for_text
    base.guidance_side_for_text = ai_guidance_side_for_text
    base.guidance_segment_for_text = ai_guidance_segment_for_text
    base.reset_to_not_policy = ai_reset_to_not_policy
    base.force_guidance_policy = ai_force_guidance_policy
    base.normalize_classification = ai_normalize_classification
    base.calibrate_with_candidate_context = ai_calibrate_with_candidate_context
    base.compact_record = ai_compact_record
    base.candidate_from_existing_candidate = ai_candidate_from_existing_candidate

    original_classify_one_candidate = base.classify_one_candidate

    def classify_one_candidate_with_aliases(candidate: Dict[str, Any], args: argparse.Namespace, models: Sequence[str]) -> Dict[str, Any]:
        return add_ai_aliases(original_classify_one_candidate(candidate, args, models))

    base.classify_one_candidate = classify_one_candidate_with_aliases


def ensure_arg(argv: List[str], flag: str, value: str) -> None:
    if flag not in argv:
        argv.extend([flag, value])


def ensure_flag(argv: List[str], flag: str) -> None:
    if flag not in argv:
        argv.append(flag)


def ai_defaults(argv: Optional[Sequence[str]]) -> List[str]:
    args = list(argv if argv is not None else sys.argv[1:])
    if not args:
        return args
    command = args[0]
    rest = args[1:]
    if command == "classify":
        ensure_arg(rest, "--input", "dummy")
        ensure_arg(rest, "--existing-candidates", "outputs/policy_packages/artificial_intelligence/candidates.jsonl")
        ensure_arg(rest, "--output-dir", "outputs/ai_policy_panel")
        ensure_arg(rest, "--candidates-name", "ai_candidates.jsonl")
        ensure_arg(rest, "--classified-name", "ai_classified.jsonl")
        ensure_arg(rest, "--min-candidate-score", "5")
    elif command == "panel":
        ensure_arg(rest, "--classified", "outputs/ai_policy_panel/ai_classified.jsonl")
        ensure_arg(rest, "--output-dir", "outputs/ai_policy_panel")
        ensure_arg(rest, "--documents-csv", "ai_policy_documents.csv")
        ensure_arg(rest, "--expanded-csv", "ai_policy_expanded_city_month.csv")
        ensure_arg(rest, "--panel-csv", "ai_policy_city_month_panel.csv")
        ensure_arg(rest, "--central-panel-csv", "ai_policy_central_month_panel.csv")
        ensure_arg(rest, "--province-panel-csv", "ai_policy_province_month_panel.csv")
        ensure_arg(rest, "--prefecture-panel-csv", "ai_policy_prefecture_month_panel.csv")
        ensure_arg(rest, "--summary-json", "ai_policy_summary.json")
    return [command] + rest


def main(argv: Optional[Sequence[str]] = None) -> None:
    patch_base()
    base.main(ai_defaults(argv))


if __name__ == "__main__":
    main()
