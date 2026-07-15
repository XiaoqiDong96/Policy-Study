#!/usr/bin/env python3
"""
Stream, classify, and aggregate Chinese NEV industrial policy documents.

The pipeline is built for the 84GB JSON array in this workspace. It first
uses high-recall keyword filtering and BeautifulSoup HTML cleanup, then asks a
local Ollama model to make the narrower industrial-policy judgment and tool
classification inspired by Fang, Li, and Lu (2025).
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOCAL_DEPS = PROJECT_ROOT / ".codex_deps"
if LOCAL_DEPS.exists():
    sys.path.insert(0, str(LOCAL_DEPS))

try:
    import ijson
except Exception as exc:  # pragma: no cover - dependency error is user-facing.
    raise SystemExit(
        "Missing dependency ijson. Install with: python -m pip install --target .codex_deps ijson"
    ) from exc

try:
    from bs4 import BeautifulSoup
except Exception as exc:  # pragma: no cover - dependency error is user-facing.
    raise SystemExit(
        "Missing dependency beautifulsoup4. Install with: python -m pip install --target .codex_deps beautifulsoup4"
    ) from exc


DOMAIN_LABEL = "NEV"


ADMIN_PROVINCES = {
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
}

MUNICIPALITIES = {"北京市", "天津市", "上海市", "重庆市"}

CENTRAL_MARKERS = (
    "国务院",
    "全国人大",
    "全国人民代表大会",
    "中央",
    "国家",
    "财政部",
    "工业和信息化部",
    "工信部",
    "发改委",
    "国家发展改革委",
    "商务部",
    "科技部",
    "交通运输部",
    "生态环境部",
    "市场监管总局",
    "税务总局",
    "海关总署",
    "银保监",
    "证监",
)

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

WEAK_NEV_TERMS = ["新能源", "汽车", "车辆", "电动车", "充电", "换电", "电池"]

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

TOOL_DEFS: Dict[str, Dict[str, str]] = {
    "credit_finance": {"label": "Credit and Finance", "group": "fiscal_financial"},
    "tax_incentives": {"label": "Tax Incentives", "group": "fiscal_financial"},
    "equity_support": {"label": "Equity Support", "group": "fiscal_financial"},
    "fiscal_subsidies": {"label": "Fiscal Subsidies", "group": "fiscal_financial"},
    "industrial_fund": {"label": "Industrial Fund", "group": "entry_regulation"},
    "promote_entrepreneurship": {"label": "Promote Entrepreneurship", "group": "entry_regulation"},
    "investment_policy": {"label": "Investment Policy", "group": "entry_regulation"},
    "business_environment": {"label": "Improving Business Environment", "group": "entry_regulation"},
    "market_access_regulation": {"label": "Market Access and Regulation", "group": "entry_regulation"},
    "trade_protection": {"label": "Trade Protection", "group": "entry_regulation"},
    "labor_policy": {"label": "Labor Policy", "group": "input_policy"},
    "preferential_land_supply": {"label": "Preferential Land Supply", "group": "input_policy"},
    "infrastructure_investment": {"label": "Infrastructure Investment", "group": "input_policy"},
    "technology_rd_adoption": {"label": "Technology R&D and Adoption", "group": "input_policy"},
    "environmental_policy": {"label": "Environmental Policy", "group": "input_policy"},
    "consumer_subsidy": {"label": "Consumer Subsidy", "group": "demand_side"},
    "government_procurement": {"label": "Government Procurement", "group": "demand_side"},
    "industrial_promotion": {"label": "Industrial Promotion", "group": "demand_side"},
    "industrial_cluster": {"label": "Promote Industrial Cluster", "group": "supply_chain"},
    "localization_policy": {"label": "Localization Policy", "group": "supply_chain"},
}

TOOL_GROUPS = ["fiscal_financial", "entry_regulation", "input_policy", "demand_side", "supply_chain"]
TONES = ["support", "restrict", "mixed", "neutral", "uncertain"]
TIMINGS = ["ex_ante", "ex_post", "mixed", "uncertain"]
SIDES = ["supply", "demand", "both", "ecosystem", "uncertain"]
MEASURE_SPECIFICITIES = ["guidance_only", "specific_measures", "mixed", "uncertain"]


SYSTEM_PROMPT = """你是严谨的中国产业政策研究助理。请根据给定政府文件的标题、元数据和正文片段进行结构化编码。

本任务采用 Fang, Li, and Lu (2025), Decoding China's Industrial Policies 的窄口径定义。产业政策是政府为了改变长期经济结构，对特定产业或特定经济活动采取的选择性、定向性干预：政府影响不同行业的相对价格，或用其能够影响/控制的资源配置手段，引导资源流向特定产业或活动。

判断是否为产业政策必须同时满足四个条件：
1. 政策主体是政府或政府部门，包括中央、省、市及其所属部门；纯公司、协会、民间主体文本不是产业政策。
2. 文本包含政府政策措施或明确的导向性政策安排。这里的“政策措施”不仅包括补贴、税收、准入、监管、项目、采购等具体工具，也包括正式规划、指导意见、战略纲要中对特定产业/经济活动的明确优先方向、发展目标、重点任务、工程安排或资源配置导向。仅报告进展、总结成绩、一般愿景、口号、政府搬迁、人事招聘/任免等不是产业政策。
3. 文本直接偏向特定产业或特定经济活动；不针对具体产业/活动的一般政策不是产业政策。即使一般政策可能间接受益某些产业，也不要据此推断为产业政策。
4. 政策目标影响长期经济结构或资源配置；仅应对短期冲击、短期周期波动或临时事务的措施通常不是产业政策。

目标产业必须是“直接目标”，不要把只被提及、弱相关、间接受益或溢出受益的产业算作目标产业。对于新能源汽车研究，只有当文件直接针对新能源汽车整车、纯电动/插电混动/燃料电池汽车、动力电池、车用氢能、充换电/换电基础设施，或与新能源汽车直接绑定的智能网联汽车时，才可判为新能源汽车相关。

请特别注意：产业政策不要求一定有补贴标准、申报细则或可操作项目。正式规划、纲要、指导意见、行动计划中，如果明确把新能源汽车或其直接产业链列为优先主题、重点方向、重点任务、重大工程、发展目标、技术路线或产业布局，也属于“导向型产业政策”，用 measure_specificity=`guidance_only` 标记。不要仅因“没有具体补贴/准入/项目细则”而判 false。

请把“本文件出台的政策”和“本文件引用/回顾的既有政策”严格区分。人大政协建议答复、预算执行决议、统计公报、年度报告、工作报告、总结材料中，即使提到“已免征新能源汽车购置税/车船税”“新能源汽车产销量增长”“此前已支持充电桩建设”，通常只是引用或回顾，不代表该文件本身出台新能源汽车产业政策，应判 false。

产业政策可以是支持性、监管性或抑制性。监管标准、准入规则、环保监管、质量安全监管、市场监管等，只要直接针对新能源汽车产业或其直接产业链并影响资源配置/进入/生产/需求，也可构成产业政策。反之，一般交通管理、一般环保治理、一般数字经济、一般科技创新、一般制造业、一般绿色低碳政策，即使提到新能源汽车，也不能自动算作新能源汽车产业政策。

请使用对抗式判断：先写出“它不是新能源汽车产业政策”的最强理由，再按上述四条件和直接目标原则作最终判断。若证据不足、缺少具体措施、缺少新能源汽车直接目标、或难以从片段确认，必须判 false。只输出 JSON，不要输出解释性正文。"""


USER_PROMPT_TEMPLATE = """请分类以下政府文件是否属于“新能源汽车产业政策”，并在通过时给出政策工具和维度。

新能源汽车范围包括：新能源汽车整车、纯电动/插电混动/燃料电池汽车、动力电池、车用氢能、充换电/换电基础设施、智能网联汽车中与新能源汽车产业直接相关的部分。

请严格执行 Fang, Li, and Lu (2025) 的窄口径判定树。除 B 项允许“明确导向性政策安排”外，任一项答案为“否”或“不确定”，最终就必须判为：
is_nev_related=false, is_industrial_policy=false, policy_tools=[], strength_score=0, coverage_breadth_score=0。

判定树：
A. 政策主体是否为政府或政府部门？
   - 是：继续。
   - 否/不确定：判 false。
B. 文本是否包含具体政府政策措施，或正式、明确、直接针对新能源汽车产业的导向性政策安排？
   - 是：继续。
   - 否/不确定：判 false。
   - 具体政府政策措施：补贴、税收、融资、基金、采购、准入、监管、标准、项目、示范、基础设施建设、研发支持、土地/劳动/供应链等工具。
   - 导向性政策安排：正式规划/纲要/指导意见中，把新能源汽车或其直接产业链列为优先产业、重点方向、重点任务、重大工程、发展目标、资源配置方向，即使没有补贴标准或申报细则，也可属于产业政策。此类用 measure_specificity=`guidance_only` 标记。
   - 如果文件明确将“低能耗与新能源汽车”“新能源汽车”“智能网联新能源汽车”“动力电池”“充换电基础设施”等列为优先主题、重点工程或发展任务，B 项应判“是”，不能因为没有补贴标准而判 false。
   - 不合格的一般愿景：只说“鼓励发展新兴产业”“推进绿色低碳”“加强科技创新”等泛泛表述，且没有新能源汽车直接目标。
C. 是否直接偏向新能源汽车产业、直接产业链或新能源汽车专属经济活动？
   - 是：继续。
   - 否/不确定：判 false。
   - 注意：“新能源”“汽车”“车辆”“电池”“充电”“智能网联”“绿色交通”“智慧城市”泛泛出现，不算直接目标。
   - 不要把只被提及、弱相关、间接受益或溢出受益的产业算作直接目标。例如新能源汽车政策可能让电池企业受益，但只有文件专门提出动力电池政策时，动力电池才算直接目标。
D. 该措施是否意在影响长期经济结构或资源配置，例如相对价格、进入退出、生产研发、融资土地劳动等投入、基础设施、采购需求、供应链或监管约束？
   - 是：可判 true。
   - 否/不确定：判 false。

统一排除规则：
- 答复人大政协建议/提案、统计公报、年度报告、工作报告、预算执行情况、预算草案、决算、预算决议、工作分工、会议培训竞赛通知、单纯名单/目录公告、普通交通安全/消防/停车/限行管理、一般环境治理、一般数字经济或智慧城市政策，除非该文件本身同步发布实施方案/办法/标准/目录/资金申报规则，否则判 false。
- 对上述答复/报告/预算/决议类文本，不能把正文中引用的既有政策或成绩当成本文件政策措施。例如“已免征新能源汽车购置税/车船税”“新能源汽车产销量增长”“开展节能减排示范”“加快过充电基础设施建设的工作回顾”，均不足以判 true。
- 标准、准入、监管、目录类文件不是自动排除；如果它们直接针对新能源汽车/动力电池/充换电/车用氢能并改变市场准入、生产条件、质量安全、补贴资格、采购资格或监管约束，可判为监管性/限制性/混合型产业政策。
- 国家/地方规划、纲要、指导意见不是自动排除；如果新能源汽车或其直接产业链是明确直接目标，且文件设置了发展方向、重点任务、重大工程、技术路线、产业布局或资源配置导向，可判为导向型产业政策，measure_specificity=`guidance_only` 或 `mixed`。
- 智能网联汽车只有在与新能源汽车、充换电、动力电池、车用能源或新能源车示范应用直接绑定时才纳入；单纯车联网、智慧交通、智能制造不纳入。
- 充电、电池、氢能只有在车用或新能源汽车上下文中才纳入；普通消费电池、储能、电力系统、建筑充电安全不纳入。
- 冷链物流、农村物流、商贸流通、数字赋能、绿色低碳、农村基础设施等政策，只有在新能源汽车或充换电/新能源物流车等被列为直接目标时才纳入；如果只是可能间接提升新能源汽车产业链、间接带动新能源物流车需求，必须判 false。
- 如果你认为“可能是”，但证据不完整，为了跨模型一致性，请判 false，并把原因写入 adversarial_not_policy_case 和 decision_reason。
- decision_reason 必须以且只能以 `结论=是；` 或 `结论=否；` 开头，并且必须与 is_nev_related、is_industrial_policy、policy_tools、strength_score 完全一致。若结论=否，则两个布尔字段必须都是 false，policy_tools 必须为空，strength_score 和 coverage_breadth_score 必须为 0。
- 严禁输出“结论=是”但理由中又承认“不符合四个条件/不能判定为产业政策/没有新能源汽车直接目标”。出现这些情况时，必须改为“结论=否”。
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
- policy_side: supply / demand / both / ecosystem / uncertain。supply 面向企业生产、研发、投资、土地、融资、供应链；demand 面向消费者购买、公交出租采购、政府采购、市场推广；ecosystem 面向充换电基础设施、标准平台、公共服务且供需难分。
- measure_specificity: guidance_only / specific_measures / mixed / uncertain。guidance_only 表示只有正式产业导向、规划目标、重点任务、发展方向或资源配置方向，但没有可操作的申报、补贴、准入、监管、项目、采购等细则；specific_measures 表示有明确具体工具或执行安排；mixed 表示二者都有。

强度 strength_score 取 0-5：
0=不是政策；1=泛泛表述；2=有方向但措施弱；3=有具体措施或责任；4=有资金、资格、指标、期限、项目或监管机制；5=有明确预算/补贴标准/强制指标/处罚/考核等高约束安排。

覆盖广度 coverage_breadth_score 取 0-5：综合目标环节数量、工具数量、执行主体数量、空间覆盖和产业链覆盖。

置信度字段必须区分两种含义：
- confidence_is_nev_related / confidence_is_industrial_policy 表示“是”的倾向或概率；明确判否时应接近 0，明确判是时应接近 1。
- classification_confidence 表示你对最终“是/否”判定本身的把握；明确判否也应较高，例如 0.75-0.95。只有证据不足、边界案例、可能误判时才低。

必须返回一个 JSON 对象，字段如下：
{{
  "is_nev_related": true,
  "is_industrial_policy": true,
  "confidence_is_nev_related": 0.0,
  "confidence_is_industrial_policy": 0.0,
  "classification_confidence": 0.0,
  "false_positive_risk": "low",
  "adversarial_not_policy_case": "最强反方理由，20-80字",
  "decision_reason": "结论=是；最终判断理由，20-120字。若否必须写结论=否；...",
  "direct_target_evidence": "原文中证明新能源汽车直接目标的短语；若否则为空",
  "measure_or_guidance_evidence": "原文中证明具体措施或导向安排的短语；若否则为空",
  "policy_tone": "support",
  "timing": "ex_ante",
  "policy_side": "supply",
  "measure_specificity": "specific_measures",
  "policy_tools": ["fiscal_subsidies"],
  "target_segments": ["整车"],
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

STANDARD_SYSTEM_PROMPT = SYSTEM_PROMPT.replace(
    "请使用对抗式判断：先写出“它不是新能源汽车产业政策”的最强理由，再按上述四条件和直接目标原则作最终判断。",
    "请进行独立结构化判断：直接按上述四条件和直接目标原则作最终判断，不进行多轮对抗、辩论或自我反驳。",
)

STANDARD_USER_PROMPT_TEMPLATE = USER_PROMPT_TEMPLATE.replace(
    "adversarial_not_policy_case\": \"最强反方理由，20-80字\"",
    "adversarial_not_policy_case\": \"若判否或有误判风险，简述排除/风险理由，20-80字\"",
)

BOUNDARY_VOTE_SYSTEM_PROMPT = """你是严谨的中国产业政策研究助理。任务只做边界样本的第二票复核：判断给定政府文件是否属于“新能源汽车产业政策”。

采用 Fang, Li, and Lu (2025), Decoding China's Industrial Policies 的窄口径定义：产业政策是政府为了改变长期经济结构，对特定产业或特定经济活动采取的选择性、定向性干预。

必须同时满足：
1. 主体是政府或政府部门。
2. 文件本身包含政策措施，或正式、明确、直接针对新能源汽车产业的导向性政策安排。
3. 直接目标是新能源汽车整车、纯电/插混/燃料电池汽车、动力电池、车用氢能、充换电/换电基础设施，或与新能源汽车直接绑定的智能网联汽车。
4. 意在影响长期经济结构或资源配置。

正式规划、纲要、指导意见、行动计划中，如果把新能源汽车或直接产业链列为优先方向、重点任务、重大工程、发展目标、产业布局或资源配置方向，即使没有补贴细则，也可判为产业政策。

排除：人大政协建议/提案答复、预算/决算/统计/年度报告/工作总结、会议培训竞赛通知、名单公告、一般交通/环保/数字经济/科技创新/智慧城市政策。除非该文件本身同步发布针对新能源汽车的实施方案、办法、标准、目录、资金申报或监管规则，否则判否。不要把正文中引用或回顾的既有政策当成本文件政策。

只输出一个 JSON 对象，不要输出解释性正文。"""

BOUNDARY_VOTE_USER_PROMPT_TEMPLATE = """请只做第二票：判断以下文件是否属于“新能源汽车产业政策”。

如果证据不足、只是提及新能源汽车、只是回顾既有政策、或不是文件本身出台政策，请判否。

返回 JSON 字段：
{{
  "is_nev_related": true,
  "is_industrial_policy": true,
  "confidence_is_nev_related": 0.0,
  "confidence_is_industrial_policy": 0.0,
  "classification_confidence": 0.0,
  "false_positive_risk": "low",
  "adversarial_not_policy_case": "若判否或有误判风险，简述理由，20-80字",
  "decision_reason": "结论=是；或结论=否；20-100字",
  "direct_target_evidence": "证明新能源汽车直接目标的原文短语；若无则为空",
  "measure_or_guidance_evidence": "证明政策措施或导向安排的原文短语；若无则为空"
}}

置信度含义：
- confidence_is_industrial_policy 表示“是产业政策”的概率；明确否应接近 0，明确是应接近 1。
- classification_confidence 表示对最终是/否判断的把握；明确否也可以是 0.8 以上。

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


def norm_space(text: str) -> str:
    text = text.replace("\u3000", " ").replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def clean_text(record: Dict[str, Any]) -> str:
    html = record.get("detail_html") or ""
    raw = record.get("detail_flag") or ""
    if html:
        try:
            soup = BeautifulSoup(html, "html.parser")
            text = soup.get_text("\n")
        except Exception:
            text = raw
    else:
        text = raw
    return norm_space(text or "")


def iter_json_records(path: Path, prefix: str = "item") -> Iterator[Dict[str, Any]]:
    if path.suffix.lower() in {".jsonl", ".ndjson"}:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    yield json.loads(line)
        return

    with path.open("rb") as fh:
        for obj in ijson.items(fh, prefix):
            yield obj


class PrefixFileReader:
    def __init__(self, fh: Any, prefix: bytes) -> None:
        self.fh = fh
        self.prefix = prefix

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            data = self.prefix + self.fh.read()
            self.prefix = b""
            return data
        if self.prefix:
            out = self.prefix[:size]
            self.prefix = self.prefix[size:]
            if len(out) == size:
                return out
            return out + self.fh.read(size - len(out))
        return self.fh.read(size)


def find_last_records_start(path: Path, record_count: int, chunk_size: int = 64 * 1024 * 1024) -> int:
    marker = re.compile(rb"(\r?\n)(  \{\r?\n    \"id\":)")
    file_size = path.stat().st_size
    positions = set()
    carry = b""
    end = file_size
    overlap = 64

    with path.open("rb") as fh:
        while end > 0 and len(positions) < record_count:
            start = max(0, end - chunk_size)
            fh.seek(start)
            data = fh.read(end - start) + carry
            for match in marker.finditer(data):
                positions.add(start + match.start(2))
            carry = data[:overlap]
            end = start

    if len(positions) < record_count:
        return 0
    return sorted(positions)[-record_count]


def iter_last_json_records(path: Path, record_count: int) -> Iterator[Dict[str, Any]]:
    if record_count <= 0 or path.suffix.lower() in {".jsonl", ".ndjson"}:
        yield from iter_json_records(path)
        return

    start_offset = find_last_records_start(path, record_count)
    if start_offset <= 0:
        yield from iter_json_records(path)
        return

    print(f"Using last-records window: n={record_count:,} start_offset={start_offset:,}", flush=True)
    with path.open("rb") as fh:
        fh.seek(start_offset)
        reader = PrefixFileReader(fh, b"[")
        for obj in ijson.items(reader, "item"):
            yield obj


def iter_selected_records(path: Path, args: argparse.Namespace) -> Iterator[Dict[str, Any]]:
    if getattr(args, "last_records", 0):
        yield from iter_last_json_records(path, args.last_records)
    else:
        yield from iter_json_records(path, args.json_prefix)


def candidate_score(title: str, text: str) -> Tuple[int, List[str], List[str]]:
    hay_title = title or ""
    hay = f"{hay_title}\n{text[:20000]}"
    hits = [term for term in NEV_TERMS if term in hay]
    weak_hits: List[str] = []
    weak_patterns = [
        ("新能源+车", r"新能源.{0,40}(汽车|车辆|车)|(?:汽车|车辆|车).{0,40}新能源"),
        ("充换电+车", r"(充电|换电|充换电).{0,50}(汽车|车辆|公交|出租|物流)|(?:汽车|车辆|公交|出租|物流).{0,50}(充电|换电|充换电)"),
        ("电池+车", r"(动力|车用|汽车|车辆|燃料).{0,40}电池|电池.{0,40}(动力|车用|汽车|车辆|燃料)"),
        ("电动车应用", r"电动车.{0,50}(公交|出租|物流|汽车|推广|应用|充电|换电)"),
    ]
    for label, pattern in weak_patterns:
        if re.search(pattern, hay):
            weak_hits.append(label)
    if not hits and not weak_hits:
        return 0, [], []

    score = 0
    if hits:
        score += 2 + min(3, len(set(hits)))
    if weak_hits:
        score += 1
    policy_hits = [term for term in POLICY_TERMS if term in hay]
    if policy_hits:
        score += min(3, len(set(policy_hits)) // 2 + 1)
    title_hits = [term for term in NEV_TERMS if term in hay_title]
    if title_hits:
        score += 3
    fp = [term for term in FALSE_POSITIVE_HINTS if term in hay_title[:120] or term in hay[:1500]]
    if fp:
        score -= 1
    return score, sorted(set(hits + weak_hits + [t for t in WEAK_NEV_TERMS if t in hay])), sorted(set(policy_hits))


def keyword_windows(text: str, terms: Sequence[str], max_chars: int = 6000, radius: int = 700) -> str:
    if len(text) <= max_chars:
        return text
    spans: List[Tuple[int, int]] = [(0, min(1200, len(text)))]
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
    spans.append((max(0, len(text) - 700), len(text)))
    spans = sorted(spans)
    merged: List[Tuple[int, int]] = []
    for s, e in spans:
        if not merged or s > merged[-1][1] + 80:
            merged.append((s, e))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
    chunks: List[str] = []
    used = 0
    for s, e in merged:
        chunk = text[s:e]
        if used + len(chunk) > max_chars:
            chunk = chunk[: max(0, max_chars - used)]
        if chunk:
            chunks.append(chunk)
            used += len(chunk)
        if used >= max_chars:
            break
    return "\n...\n".join(chunks)


LONG_TEXT_EVIDENCE_TERMS = sorted(
    set(
        NEV_TERMS
        + WEAK_NEV_TERMS
        + POLICY_TERMS
        + [
            "补贴",
            "奖励",
            "奖补",
            "扶持",
            "支持",
            "免征",
            "减免",
            "税收",
            "贷款",
            "融资",
            "基金",
            "采购",
            "推广应用",
            "示范应用",
            "试点",
            "准入",
            "标准",
            "监管",
            "处罚",
            "淘汰",
            "目录",
            "申报",
            "认定",
            "考核",
            "验收",
            "责任单位",
            "牵头单位",
            "实施",
            "重点任务",
            "重点工程",
            "重大工程",
            "行动计划",
            "发展目标",
            "产业链",
            "产业集群",
            "技术路线",
            "基础设施",
            "充电设施",
            "换电站",
            "动力电池",
            "燃料电池",
            "智能网联新能源汽车",
        ]
    )
)


def split_policy_paragraphs(text: str) -> List[str]:
    paragraphs = [p.strip() for p in re.split(r"\n+", text) if p.strip()]
    if len(paragraphs) >= 8:
        return paragraphs

    sentence_like = re.split(r"(?<=[。；;])", text)
    chunks: List[str] = []
    buf = ""
    for piece in sentence_like:
        piece = piece.strip()
        if not piece:
            continue
        if len(buf) + len(piece) <= 450:
            buf += piece
        else:
            if buf:
                chunks.append(buf)
            buf = piece
    if buf:
        chunks.append(buf)
    return chunks or ([text] if text else [])


def is_policy_heading(paragraph: str) -> bool:
    compact = paragraph.strip()
    if len(compact) > 90:
        return False
    return bool(
        re.match(r"^([一二三四五六七八九十]+、|（[一二三四五六七八九十]+）|\([一二三四五六七八九十]+\)|第[一二三四五六七八九十0-9]+[章节条]|[0-9]+[.、])", compact)
        or any(term in compact for term in ["总体要求", "主要目标", "重点任务", "保障措施", "政策措施", "申报条件", "支持范围"])
    )


def paragraph_evidence_score(paragraph: str, terms: Sequence[str], position: int, total: int) -> int:
    score = 0
    direct_hits = sum(1 for term in terms if term and term in paragraph)
    nev_hits = sum(1 for term in NEV_TERMS if term in paragraph)
    weak_hits = sum(1 for term in WEAK_NEV_TERMS if term in paragraph)
    policy_hits = sum(1 for term in POLICY_TERMS if term in paragraph)
    evidence_hits = sum(1 for term in LONG_TEXT_EVIDENCE_TERMS if term in paragraph)
    score += min(25, direct_hits * 8)
    score += min(30, nev_hits * 10 + weak_hits * 4)
    score += min(18, policy_hits * 3)
    score += min(18, evidence_hits * 2)
    if nev_hits and policy_hits:
        score += 35
    if nev_hits and any(term in paragraph for term in ["补贴", "免征", "采购", "准入", "标准", "目录", "基础设施", "重点任务", "重大工程"]):
        score += 20
    if re.search(r"\d+(\.\d+)?\s*(%|万元|亿元|辆|个|座|台|年|月|日|公里|千瓦|MW|GW)", paragraph):
        score += 8
    if any(term in paragraph for term in ["责任单位", "牵头单位", "完成时限", "申报", "验收", "考核", "监督", "处罚"]):
        score += 10
    if is_policy_heading(paragraph):
        score += 8
    if position < 6:
        score += 6 - position
    if total and position >= total - 3:
        score += 3
    return score


def compress_long_policy_text(text: str, terms: Sequence[str], max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    if max_chars <= 1200:
        return keyword_windows(text, terms, max_chars=max_chars, radius=max(220, max_chars // 8))

    budget = max_chars
    header = (
        f"【长文档压缩摘要】原文约{len(text):,}字，超过模型上下文。以下为自动证据保留式压缩："
        "优先保留标题开头、条款标题、含新能源汽车直接目标和政策工具的段落、数字指标、责任机制与结尾。"
    )
    paragraphs = split_policy_paragraphs(text)
    selected: Dict[int, str] = {}

    for idx in range(min(4, len(paragraphs))):
        selected[idx] = paragraphs[idx]
    for idx in range(max(0, len(paragraphs) - 2), len(paragraphs)):
        selected[idx] = paragraphs[idx]

    scored = [
        (paragraph_evidence_score(paragraph, terms, idx, len(paragraphs)), idx, paragraph)
        for idx, paragraph in enumerate(paragraphs)
    ]
    scored.sort(key=lambda item: (-item[0], item[1]))

    used = len(header) + 16 + sum(len(p) + 8 for p in selected.values())
    for score, idx, paragraph in scored:
        if score <= 0:
            break
        if idx in selected:
            continue
        add_len = len(paragraph) + 8
        if used + add_len > budget:
            continue
        selected[idx] = paragraph
        used += add_len
        if used >= budget * 0.96:
            break

    ordered = [selected[idx] for idx in sorted(selected)]
    compressed = header + "\n" + "\n...\n".join(ordered)
    if len(compressed) > max_chars:
        compressed = compressed[:max_chars]
    return compressed


def build_policy_evidence_pack(
    text: str,
    terms: Sequence[str],
    max_chars: int,
    fulltext_threshold: int = 2500,
) -> str:
    if not text:
        return ""
    if len(text) <= min(max_chars, fulltext_threshold):
        return text
    if max_chars <= 1200:
        return keyword_windows(text, terms, max_chars=max_chars, radius=max(220, max_chars // 8))

    paragraphs = split_policy_paragraphs(text)
    total = len(paragraphs)
    selected: Dict[int, Tuple[str, str]] = {}

    def add(idx: int, reason: str) -> None:
        if 0 <= idx < total and paragraphs[idx].strip():
            selected[idx] = (paragraphs[idx], reason)

    for idx in range(min(3, total)):
        add(idx, "文首/标题/总则")
    for idx in range(max(0, total - 1), total):
        add(idx, "结尾/附则")

    hit_indices: List[int] = []
    for idx, paragraph in enumerate(paragraphs):
        has_target = any(term and term in paragraph for term in terms)
        has_nev = any(term in paragraph for term in NEV_TERMS + WEAK_NEV_TERMS)
        if has_target or has_nev:
            hit_indices.append(idx)
            add(idx, "新能源汽车直接目标词")
            if idx > 0 and is_policy_heading(paragraphs[idx - 1]):
                add(idx - 1, "相关条款标题")
            if idx + 1 < total and len(paragraphs[idx + 1]) <= 260:
                add(idx + 1, "目标词后续短段")

    scored = [
        (paragraph_evidence_score(paragraph, terms, idx, total), idx, paragraph)
        for idx, paragraph in enumerate(paragraphs)
    ]
    scored.sort(key=lambda item: (-item[0], item[1]))
    for score, idx, paragraph in scored[: min(80, len(scored))]:
        if score < 12:
            break
        reason = "政策工具/数字/责任证据"
        if any(term in paragraph for term in NEV_TERMS + WEAK_NEV_TERMS):
            reason = "新能源汽车目标+政策证据"
        if is_policy_heading(paragraph):
            reason = "条款标题"
        add(idx, reason)
        if idx > 0 and is_policy_heading(paragraphs[idx - 1]):
            add(idx - 1, "相关条款标题")

    header = (
        f"【确定性全文预处理证据包】原文约{len(text):,}字，共{total:,}段。"
        "本证据包未使用大模型；按规则保留文首、结尾、条款标题、含新能源汽车直接目标词的段落、"
        "含政策工具/数字指标/责任机制/申报考核的高分段落。若下列证据仅为综合政策中的附带罗列，"
        "仍应按直接目标原则判否。"
    )
    budget = max_chars
    chunks = [header]
    used = len(header) + 8
    omitted = 0

    for idx in sorted(selected):
        paragraph, reason = selected[idx]
        label = f"【段落{idx + 1}/{total}；{reason}】"
        chunk = f"{label}\n{paragraph}"
        add_len = len(chunk) + 8
        if used + add_len > budget:
            remaining = budget - used - len(label) - 12
            if remaining > 120:
                chunks.append(f"{label}\n{paragraph[:remaining]}...")
                used = budget
            else:
                omitted += 1
            break
        chunks.append(chunk)
        used += add_len

    omitted += max(0, len(selected) - (len(chunks) - 1))
    if omitted:
        chunks.append(f"【省略说明】因长度上限，另有{omitted}个候选证据段未纳入。")
    out = "\n...\n".join(chunks)
    return out[:max_chars]


def prepare_llm_body(
    body: str,
    terms: Sequence[str],
    args: argparse.Namespace,
) -> Tuple[str, str, int, int, float]:
    original_len = len(body)
    max_chars = max(0, int(getattr(args, "max_body_chars", 0) or 0))
    mode = getattr(args, "long_doc_mode", "compress")
    if mode == "evidence_pack" and max_chars:
        prepared = build_policy_evidence_pack(body, terms, max_chars=max_chars)
        input_mode = "evidence_pack" if len(prepared) < original_len else "full_text"
        return prepared, input_mode, original_len, len(prepared), (len(prepared) / original_len if original_len else 1.0)
    if not max_chars or original_len <= max_chars:
        return body, "full_text", original_len, original_len, 1.0

    if mode == "window":
        prepared = keyword_windows(
            body,
            terms,
            max_chars=max_chars,
            radius=max(300, min(900, max_chars // 8)),
        )
        input_mode = "keyword_windows"
    elif mode == "truncate":
        prepared = body[:max_chars]
        input_mode = "truncated_long_text"
    else:
        prepared = compress_long_policy_text(body, terms, max_chars=max_chars)
        input_mode = "compressed_long_text"

    return prepared, input_mode, original_len, len(prepared), (len(prepared) / original_len if original_len else 1.0)


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        val = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(val) or math.isinf(val):
        return default
    return max(0.0, min(1.0, val))


def safe_int(value: Any, default: int = 0, low: int = 0, high: int = 5) -> int:
    try:
        val = int(round(float(value)))
    except (TypeError, ValueError):
        return default
    return max(low, min(high, val))


def canonical_list(values: Any, allowed: Optional[set] = None) -> List[str]:
    if values is None:
        return []
    if isinstance(values, str):
        parts = re.split(r"[,，;；、\n]+", values)
    elif isinstance(values, list):
        parts = values
    else:
        parts = [values]
    out: List[str] = []
    for item in parts:
        val = str(item).strip()
        if not val:
            continue
        if allowed is not None and val not in allowed:
            continue
        if val not in out:
            out.append(val)
    return out


def contains_direct_nev_term(text: str) -> bool:
    return any(term in text for term in NEV_TERMS)


def first_direct_nev_term(text: str) -> str:
    for term in NEV_TERMS:
        if term in text:
            return term
    return ""


DIRECT_NEV_PATTERN = "|".join(re.escape(term) for term in NEV_TERMS)
CORE_NEV_TERMS = [term for term in NEV_TERMS if term != "智能网联汽车"]
CORE_NEV_PATTERN = "|".join(re.escape(term) for term in CORE_NEV_TERMS)


def contains_core_nev_term(text: str) -> bool:
    return any(term in text for term in CORE_NEV_TERMS)


def contains_valid_nev_target(text: str) -> bool:
    if contains_core_nev_term(text):
        return True
    return bool(re.search(r"(新能源.{0,12}智能网联|智能网联.{0,12}新能源)", text))


def reset_to_not_policy(cls: Dict[str, Any], reason: str) -> Dict[str, Any]:
    out = dict(cls)
    out["is_nev_related"] = False
    out["is_industrial_policy"] = False
    out["confidence_is_industrial_policy"] = min(safe_float(out.get("confidence_is_industrial_policy")), 0.30)
    out["classification_confidence"] = max(safe_float(out.get("classification_confidence")), 0.85)
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


def negative_decision_confidence(reason: str, current: Any) -> float:
    current_val = safe_float(current)
    uncertain = bool(re.search(r"(可能|难以|不足|不确定|无法确认|证据不完整|片段|待人工|边界)", reason))
    floor = 0.55 if uncertain else 0.78
    ceiling = 0.72 if uncertain else 1.0
    return min(max(current_val, floor), ceiling)


def reply_report_or_budget_title(title: str) -> bool:
    if re.search(r"(对.*(建议|提案).*答复|提案答复|建议答复|答复的函|答复函?$|^.*答复$|办理结果|回复|复函)", title):
        return True
    if re.search(r"(国家标准公告|行业标准公告|地方标准公告|批准发布.*标准|标准修改单)", title):
        return True
    return bool(
        re.search(
            r"(统计公报|年度报告|工作报告|预算执行情况|预算草案|预算报告|"
            r"决算|关于.*预算.*决议|预算.*决议|执行情况与.*预算)",
            title,
        )
    )


def formal_policy_title(title: str) -> bool:
    return bool(
        re.search(
            r"(规划|纲要|指导意见|行动计划|实施方案|若干措施|办法|规定|标准|"
            r"通知|目录|方案|意见|细则|指南)",
            title,
        )
    )


def strong_guidance_match(text: str) -> Optional[re.Match[str]]:
    if not contains_valid_nev_target(text):
        return None
    markers = (
        r"优先主题|重点方向|重点任务|重大工程|发展目标|重点研究|技术路线|"
        r"产业布局|战略性新兴产业|培育壮大|推广应用|示范应用|"
        r"充换电基础设施建设|充电基础设施建设|换电基础设施|"
        r"加强建设|推进建设|支持建设|加快建设|研发|研究开发"
    )
    patterns = [
        rf"({CORE_NEV_PATTERN}).{{0,60}}({markers})",
        rf"({markers}).{{0,60}}({CORE_NEV_PATTERN})",
        rf"(新能源.{0,12}智能网联|智能网联.{0,12}新能源).{{0,60}}({markers})",
        rf"({markers}).{{0,60}}(新能源.{0,12}智能网联|智能网联.{0,12}新能源)",
    ]
    for pattern in patterns:
        m = re.search(pattern, text)
        if m:
            return m
    return None


def guidance_tool_for_text(text: str) -> str:
    if re.search(r"(充换电|充电基础设施|充电桩|换电站|基础设施)", text):
        return "infrastructure_investment"
    if re.search(r"(研发|研究开发|技术|创新|攻关|试验|测试)", text):
        return "technology_rd_adoption"
    if re.search(r"(产业集群|集群|基地|园区)", text):
        return "industrial_cluster"
    return "industrial_promotion"


def guidance_side_for_text(text: str) -> str:
    if re.search(r"(充换电|充电基础设施|充电桩|换电站|基础设施)", text):
        return "ecosystem"
    return "supply"


def guidance_segment_for_text(text: str) -> str:
    if re.search(r"(充换电|充电基础设施|充电桩|换电站)", text):
        return "充换电基础设施"
    if "动力电池" in text:
        return "动力电池"
    if re.search(r"(燃料电池|车用氢能|氢能汽车)", text):
        return "燃料电池/车用氢能"
    if "智能网联" in text:
        return "智能网联新能源汽车"
    return "整车"


def force_guidance_policy(cls: Dict[str, Any], evidence: str, context: str) -> Dict[str, Any]:
    out = dict(cls)
    tool = guidance_tool_for_text(context)
    segment = guidance_segment_for_text(context)
    out["is_nev_related"] = True
    out["is_industrial_policy"] = True
    out["confidence_is_nev_related"] = max(safe_float(out.get("confidence_is_nev_related")), 0.78)
    out["confidence_is_industrial_policy"] = max(safe_float(out.get("confidence_is_industrial_policy")), 0.68)
    out["classification_confidence"] = max(safe_float(out.get("classification_confidence")), 0.68)
    out["false_positive_risk"] = "medium"
    out["adversarial_not_policy_case"] = (
        out.get("adversarial_not_policy_case")
        or "可能只是综合规划中的一个领域，但原文已把新能源汽车或直接产业链列为明确任务。"
    )
    out["decision_reason"] = f"结论=是；原文明确把新能源汽车或直接产业链列为重点任务/发展方向：{evidence}"[:500]
    out["direct_target_evidence"] = first_direct_nev_term(evidence) or first_direct_nev_term(context)
    out["measure_or_guidance_evidence"] = evidence[:200]
    out["policy_tone"] = "support"
    out["timing"] = "ex_ante"
    out["policy_side"] = guidance_side_for_text(context)
    out["measure_specificity"] = "guidance_only"
    out["policy_tools"] = [tool]
    out["tool_groups"] = sorted({TOOL_DEFS[tool]["group"]})
    out["target_segments"] = sorted(set(out.get("target_segments") or []) | {segment})
    out["specific_measures"] = out.get("specific_measures") or []
    out["eligibility_conditions"] = out.get("eligibility_conditions") or []
    out["implementation_mechanisms"] = out.get("implementation_mechanisms") or []
    out["strength_score"] = max(safe_int(out.get("strength_score"), low=0, high=5), 2)
    out["coverage_breadth_score"] = max(safe_int(out.get("coverage_breadth_score"), low=0, high=5), 1)
    return out


def calibrate_with_candidate_context(candidate: Dict[str, Any], cls: Dict[str, Any]) -> Dict[str, Any]:
    title = str(candidate.get("title") or "")
    body = str(candidate.get("llm_body") or "")
    candidate_context = "\n".join(
        [
            title,
            str(candidate.get("pub_depart") or ""),
            str(candidate.get("law_type") or ""),
            body,
        ]
    )

    if reply_report_or_budget_title(title):
        return reset_to_not_policy(
            cls,
            "文件标题属于建议/提案答复、报告、统计公报、预算执行或预算决议类文本；即使引用既有新能源汽车政策或成绩，也不视为本文件出台产业政策。",
        )

    if cls.get("is_nev_related") and cls.get("is_industrial_policy") and not contains_valid_nev_target(candidate_context):
        return reset_to_not_policy(
            cls,
            "正文缺少新能源汽车、动力电池、车用氢能或充换电等核心直接目标；单独的智能网联汽车、普通锂电池、车辆或充电表述不纳入新能源汽车产业政策。",
        )

    if formal_policy_title(title):
        m = strong_guidance_match(candidate_context)
        if m:
            return force_guidance_policy(cls, m.group(0), candidate_context)

    return cls


def normalize_classification(raw: Dict[str, Any]) -> Dict[str, Any]:
    tools = canonical_list(raw.get("policy_tools"), set(TOOL_DEFS))
    groups = sorted({TOOL_DEFS[t]["group"] for t in tools})
    tone = str(raw.get("policy_tone") or "uncertain").strip()
    timing = str(raw.get("timing") or "uncertain").strip()
    side = str(raw.get("policy_side") or "uncertain").strip()
    measure_specificity = str(raw.get("measure_specificity") or "uncertain").strip()
    if tone not in TONES:
        tone = "uncertain"
    if timing not in TIMINGS:
        timing = "uncertain"
    if side not in SIDES:
        side = "uncertain"
    if measure_specificity not in MEASURE_SPECIFICITIES:
        measure_specificity = "uncertain"
    out = {
        "is_nev_related": bool(raw.get("is_nev_related", False)),
        "is_industrial_policy": bool(raw.get("is_industrial_policy", False)),
        "confidence_is_nev_related": safe_float(raw.get("confidence_is_nev_related")),
        "confidence_is_industrial_policy": safe_float(raw.get("confidence_is_industrial_policy")),
        "classification_confidence": safe_float(raw.get("classification_confidence")),
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
        "target_segments": canonical_list(raw.get("target_segments")),
        "specific_measures": canonical_list(raw.get("specific_measures")),
        "eligibility_conditions": canonical_list(raw.get("eligibility_conditions")),
        "implementation_mechanisms": canonical_list(raw.get("implementation_mechanisms")),
        "strength_score": safe_int(raw.get("strength_score"), low=0, high=5),
        "coverage_breadth_score": safe_int(raw.get("coverage_breadth_score"), low=0, high=5),
    }
    if not (out["is_nev_related"] and out["is_industrial_policy"]):
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
    reason = out.get("decision_reason", "")
    explicit_negative_conclusion = bool(
        re.search(r"^\s*(结论\s*=\s*否|结论[:：]\s*否)", reason, flags=re.IGNORECASE)
    )
    negative_conclusion = bool(
        re.search(
            r"(结论\s*=\s*否|结论[:：]\s*否|判\s*false|判为\s*false|最终判定为否|"
            r"不属于新能源汽?车产业政策|不是新能源汽?车产业政策|不属于.*产业政策|不是.*产业政策|"
            r"不符合.*窄口径|不符合.*四个条件|不符合.*产业政策判定标准|"
            r"不满足.*判定树|未满足.*条件|不能判定为.*产业政策)",
            reason,
            flags=re.IGNORECASE,
        )
    )
    rule_violation_conclusion = bool(
        re.search(
            r"(没有新能源汽?车直接目标|缺少新能源汽?车直接目标|"
            r"未明确新能源汽?车直接目标|并非直接针对新能源汽?车|"
            r"间接支持|间接受益|间接带动|间接提升|溢出受益|弱相关|"
            r"仅.*提到|只是.*提到|主要.*一般|属于.*一般|"
            r"不涉及.*资源配置|不涉及.*长期经济结构)",
            reason,
            flags=re.IGNORECASE,
        )
    )
    positive_conclusion = bool(
        re.search(
            r"(结论\s*=\s*是|结论[:：]\s*是|判\s*true|判为\s*true|最终判定为是|"
            r"属于新能源汽?车产业政策|构成新能源汽?车产业政策)",
            reason,
            flags=re.IGNORECASE,
        )
    )
    guidance_positive = bool(
        contains_direct_nev_term(reason)
        and re.search(
            r"(列为|作为|纳入|明确|提出|设置|确定).{0,30}"
            r"(优先主题|重点方向|重点任务|重大工程|发展目标|重点研究|技术路线|产业布局|规划方向)",
            reason,
            flags=re.IGNORECASE,
        )
    )
    if guidance_positive and not explicit_negative_conclusion and not rule_violation_conclusion:
        out["is_nev_related"] = True
        out["is_industrial_policy"] = True
        out["confidence_is_nev_related"] = max(out["confidence_is_nev_related"], 0.75)
        out["confidence_is_industrial_policy"] = max(out["confidence_is_industrial_policy"], 0.65)
        out["classification_confidence"] = max(out["classification_confidence"], 0.65)
        out["false_positive_risk"] = "medium"
        if out["policy_tone"] == "uncertain":
            out["policy_tone"] = "support"
        if out["timing"] == "uncertain":
            out["timing"] = "ex_ante"
        if out["policy_side"] == "uncertain":
            out["policy_side"] = "supply"
        out["measure_specificity"] = "guidance_only"
        if not out["policy_tools"]:
            out["policy_tools"] = ["industrial_promotion"]
            out["tool_groups"] = sorted({TOOL_DEFS[t]["group"] for t in out["policy_tools"]})
        if not out["direct_target_evidence"]:
            out["direct_target_evidence"] = first_direct_nev_term(reason)
        if not out["measure_or_guidance_evidence"]:
            out["measure_or_guidance_evidence"] = "优先主题/重点任务/发展目标"

    if explicit_negative_conclusion or (negative_conclusion and not guidance_positive) or rule_violation_conclusion:
        out["is_nev_related"] = False
        out["is_industrial_policy"] = False
        out["confidence_is_industrial_policy"] = min(out["confidence_is_industrial_policy"], 0.35)
        out["classification_confidence"] = negative_decision_confidence(reason, out.get("classification_confidence"))
        out["false_positive_risk"] = "high"
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
    elif out["is_nev_related"] and out["is_industrial_policy"] and not contains_direct_nev_term(
        out.get("direct_target_evidence", "")
    ):
        out["is_nev_related"] = False
        out["is_industrial_policy"] = False
        out["confidence_is_industrial_policy"] = min(out["confidence_is_industrial_policy"], 0.35)
        out["classification_confidence"] = max(out["classification_confidence"], 0.72)
        out["false_positive_risk"] = "high"
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


def extract_json_object(text: str) -> Dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    def repair_with_json_repair(candidate: str) -> Optional[Dict[str, Any]]:
        try:
            from json_repair import repair_json  # type: ignore
        except Exception:
            return None
        try:
            repaired = repair_json(candidate, return_objects=True)
        except Exception:
            return None
        if isinstance(repaired, dict):
            return repaired
        if isinstance(repaired, str):
            try:
                parsed = json.loads(repaired)
            except json.JSONDecodeError:
                return None
            return parsed if isinstance(parsed, dict) else None
        return None

    def loads_lenient(candidate: str) -> Dict[str, Any]:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
        # Some cloud models return one valid JSON object followed by a second
        # object or a short explanation despite the one-object instruction.
        # Accept the first complete object; raw_decode still rejects a broken
        # or truncated first object, which then falls through to repair logic.
        try:
            parsed, _ = json.JSONDecoder().raw_decode(candidate.lstrip())
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
        repaired_obj = repair_with_json_repair(candidate)
        if repaired_obj is not None:
            return repaired_obj

        repaired: List[str] = []
        in_string = False
        escaped = False
        for ch in candidate:
            if escaped:
                repaired.append(ch)
                escaped = False
                continue
            if ch == "\\":
                repaired.append(ch)
                escaped = True
                continue
            if ch == '"':
                repaired.append(ch)
                in_string = not in_string
                continue
            if in_string and ch in {"\n", "\r"}:
                repaired.append("\\n")
                continue
            if in_string and ch == "\t":
                repaired.append("\\t")
                continue
            repaired.append(ch)
        return json.loads("".join(repaired))

    try:
        return loads_lenient(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return loads_lenient(text[start : end + 1])
    raise ValueError("No JSON object found in LLM response")


def resolve_ollama_format(model: str, setting: str = "auto") -> Optional[str]:
    setting = (setting or "auto").strip().lower()
    if setting == "none":
        return None
    if setting == "empty":
        return ""
    if setting == "json":
        return "json"
    if model.startswith("gpt-oss:") or model == "gpt-oss":
        # Ollama Cloud gpt-oss currently returns an empty response with format=json
        # or no format key, but returns normal text with format="".
        return ""
    return "json"


def read_http_error_body(exc: urllib.error.HTTPError) -> str:
    try:
        raw = exc.read()
    except Exception:
        return ""
    try:
        return raw.decode("utf-8", errors="replace").strip()
    except Exception:
        return repr(raw[:500])


def classify_cloud_limit_message(message: str) -> str:
    lower = message.lower()
    if any(term in lower for term in ["weekly", "7 day", "7-day", "seven day"]):
        return "weekly_limit"
    if any(term in lower for term in ["session", "5 hour", "5-hour", "five hour"]):
        return "session_limit"
    if any(term in lower for term in ["quota", "usage limit", "rate limit", "too many requests", "429"]):
        return "short_rate_limit"
    return "unknown"


def call_ollama(
    prompt: str,
    model: str,
    host: str,
    timeout: int = 180,
    num_ctx: int = 24576,
    system_prompt: str = SYSTEM_PROMPT,
    ollama_format: str = "auto",
    max_retries: int = 2,
    retry_base_sleep: float = 4.0,
) -> Tuple[Dict[str, Any], str]:
    payload = {
        "model": model,
        "system": system_prompt,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0, "num_ctx": num_ctx},
    }
    format_value = resolve_ollama_format(model, ollama_format)
    if format_value is not None:
        payload["format"] = format_value
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{host.rstrip('/')}/api/generate",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    last_http_error: Optional[urllib.error.HTTPError] = None
    last_http_body = ""
    for attempt in range(max(0, max_retries) + 1):
        try:
            with opener.open(req, timeout=timeout) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as exc:
            last_http_error = exc
            last_http_body = read_http_error_body(exc)
            limit_kind = classify_cloud_limit_message(f"{exc} {last_http_body}")
            retryable = exc.code in {429, 500, 502, 503, 504}
            if not retryable or attempt >= max_retries:
                if last_http_body:
                    raise urllib.error.HTTPError(
                        exc.url,
                        exc.code,
                        f"{exc.reason}; limit_kind={limit_kind}; body={last_http_body[:500]}",
                        exc.headers,
                        None,
                    ) from exc
                raise
            sleep_for = retry_base_sleep * (2**attempt) + random.random()
            print(
                f"[LLM RETRY] model={model} http={exc.code} limit_kind={limit_kind} "
                f"attempt={attempt + 1}/{max_retries} sleep={sleep_for:.1f}s "
                f"body={last_http_body[:180]!r}",
                flush=True,
            )
            time.sleep(sleep_for)
    else:  # pragma: no cover - defensive; loop either breaks or raises.
        if last_http_error is not None:
            raise last_http_error
        raise RuntimeError("Ollama request failed before receiving a response")
    response_text = result.get("response") or ""
    try:
        return extract_json_object(response_text), response_text
    except ValueError as exc:
        head = response_text[:500].replace("\n", "\\n")
        raise ValueError(
            f"{exc}; response_head={head!r}; done_reason={result.get('done_reason')!r}; "
            f"eval_count={result.get('eval_count')!r}"
        ) from exc


def parse_model_list(models_arg: str, fallback_model: str) -> List[str]:
    raw = models_arg or fallback_model
    models = [m.strip() for m in re.split(r"[,，;；\s]+", raw) if m.strip()]
    return models or [fallback_model]


def model_company(model: str) -> str:
    name = model.lower()
    if "qwen" in name or "qwq" in name:
        return "Alibaba/Qwen"
    if "llama" in name:
        return "Meta"
    if "gemma" in name:
        return "Google"
    if "gpt-oss" in name or "gptoss" in name:
        return "OpenAI/gpt-oss"
    if "glm" in name:
        return "Zhipu/GLM"
    if "mistral" in name or "mixtral" in name:
        return "Mistral AI"
    if "deepseek" in name:
        return "DeepSeek"
    if "phi" in name:
        return "Microsoft"
    if "yi" in name:
        return "01.AI"
    return "Unknown"


def prompt_mode(args: argparse.Namespace) -> str:
    return str(getattr(args, "prompt_mode", "standard") or "standard")


def system_prompt_for_args(args: argparse.Namespace) -> str:
    mode = prompt_mode(args)
    if mode == "boundary_vote":
        return BOUNDARY_VOTE_SYSTEM_PROMPT
    return STANDARD_SYSTEM_PROMPT if mode == "standard" else SYSTEM_PROMPT


def user_prompt_template_for_args(args: argparse.Namespace) -> str:
    mode = prompt_mode(args)
    if mode == "boundary_vote":
        return BOUNDARY_VOTE_USER_PROMPT_TEMPLATE
    return STANDARD_USER_PROMPT_TEMPLATE if mode == "standard" else USER_PROMPT_TEMPLATE


def run_model_classification(candidate: Dict[str, Any], args: argparse.Namespace, model: str) -> Dict[str, Any]:
    prompt = prompt_for_candidate(candidate, args)
    try:
        raw, _response = call_ollama(
            prompt=prompt,
            model=model,
            host=args.ollama_host,
            timeout=args.llm_timeout,
            num_ctx=args.num_ctx,
            system_prompt=system_prompt_for_args(args),
            ollama_format=getattr(args, "ollama_format", "auto"),
            max_retries=getattr(args, "llm_retries", 2),
            retry_base_sleep=getattr(args, "retry_base_sleep", 4.0),
        )
        return {
            "model": model,
            "company": model_company(model),
            "classification": calibrate_with_candidate_context(candidate, normalize_classification(raw)),
            "error": "",
        }
    except (ValueError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return {
            "model": model,
            "company": model_company(model),
            "classification": normalize_classification(
                {
                    "is_nev_related": False,
                    "is_industrial_policy": False,
                    "confidence_is_nev_related": 0.0,
                    "confidence_is_industrial_policy": 0.0,
                    "classification_confidence": 0.0,
                    "decision_reason": "LLM call failed; this model is excluded from the ensemble vote.",
                }
            ),
            "error": repr(exc)[:500],
        }


def majority_value(values: List[str], default: str) -> str:
    counts: Dict[str, int] = defaultdict(int)
    for value in values:
        if value:
            counts[value] += 1
    if not counts:
        return default
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


def aggregate_model_classifications(model_results: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    successful = [r for r in model_results if not r.get("error")]
    if not successful:
        fallback = normalize_classification(
            {
                "is_nev_related": False,
                "is_industrial_policy": False,
                "confidence_is_nev_related": 0.0,
                "confidence_is_industrial_policy": 0.0,
                "classification_confidence": 0.0,
                "decision_reason": "All model calls failed; excluded from panel until rerun.",
            }
        )
        return fallback, {
            "models_requested": [r["model"] for r in model_results],
            "models_succeeded": 0,
            "models_failed": len(model_results),
            "nev_yes_votes": 0,
            "industrial_yes_votes": 0,
            "policy_yes_votes": 0,
            "policy_vote_share": 0.0,
            "strict_majority_rule": "yes_votes > successful_models / 2; ties are rejected",
            "classification_disagreement": False,
            "tool_jaccard_mean": 0.0,
        }

    n = len(successful)
    classifications = [r["classification"] for r in successful]
    nev_yes = sum(1 for c in classifications if c.get("is_nev_related"))
    industrial_yes = sum(1 for c in classifications if c.get("is_industrial_policy"))
    policy_yes = sum(1 for c in classifications if c.get("is_nev_related") and c.get("is_industrial_policy"))
    final_nev = nev_yes > n / 2
    final_policy = policy_yes > n / 2
    yes_classifications = [
        c for c in classifications if c.get("is_nev_related") and c.get("is_industrial_policy")
    ]
    source_classifications = yes_classifications if final_policy and yes_classifications else classifications

    tool_counts: Dict[str, int] = defaultdict(int)
    for c in yes_classifications:
        for tool in c.get("policy_tools", []):
            tool_counts[tool] += 1
    tool_threshold = max(1, math.floor(len(yes_classifications) / 2) + 1) if yes_classifications else 1
    tools = sorted([tool for tool, count in tool_counts.items() if count >= tool_threshold])
    if final_policy and not tools and yes_classifications:
        tools = yes_classifications[0].get("policy_tools", [])

    pairwise_jaccards: List[float] = []
    tool_sets = [set(c.get("policy_tools", [])) for c in yes_classifications]
    for i in range(len(tool_sets)):
        for j in range(i + 1, len(tool_sets)):
            union = tool_sets[i] | tool_sets[j]
            inter = tool_sets[i] & tool_sets[j]
            pairwise_jaccards.append(len(inter) / len(union) if union else 1.0)
    tool_jaccard = sum(pairwise_jaccards) / len(pairwise_jaccards) if pairwise_jaccards else (1.0 if len(tool_sets) == 1 else 0.0)

    avg_conf_nev = sum(safe_float(c.get("confidence_is_nev_related")) for c in classifications) / n
    avg_conf_policy = sum(safe_float(c.get("confidence_is_industrial_policy")) for c in classifications) / n
    avg_conf_class = sum(safe_float(c.get("classification_confidence")) for c in classifications) / n
    policy_vote_share = policy_yes / n
    disagreement = len({bool(c.get("is_nev_related") and c.get("is_industrial_policy")) for c in classifications}) > 1
    agreement_multiplier = policy_vote_share if final_policy else (1 - policy_vote_share)
    if disagreement:
        agreement_multiplier *= 0.85
    final_confidence = max(0.0, min(1.0, avg_conf_class * agreement_multiplier))

    strength_values = [safe_int(c.get("strength_score"), low=0, high=5) for c in source_classifications]
    breadth_values = [safe_int(c.get("coverage_breadth_score"), low=0, high=5) for c in source_classifications]

    merged = normalize_classification(
        {
            "is_nev_related": final_nev,
            "is_industrial_policy": final_policy,
            "confidence_is_nev_related": avg_conf_nev * (nev_yes / n),
            "confidence_is_industrial_policy": avg_conf_policy * agreement_multiplier,
            "classification_confidence": final_confidence,
            "false_positive_risk": "high" if disagreement else "low",
            "adversarial_not_policy_case": "；".join(
                [c.get("adversarial_not_policy_case", "") for c in classifications if c.get("adversarial_not_policy_case")]
            )[:300],
            "decision_reason": (
                f"模型投票：{policy_yes}/{n} 认为是新能源汽车产业政策；"
                f"{'严格多数通过' if final_policy else '未达严格多数，排除或待人工复核'}。"
            ),
            "policy_tone": majority_value([c.get("policy_tone", "") for c in source_classifications], "uncertain"),
            "timing": majority_value([c.get("timing", "") for c in source_classifications], "uncertain"),
            "policy_side": majority_value([c.get("policy_side", "") for c in source_classifications], "uncertain"),
            "measure_specificity": majority_value(
                [c.get("measure_specificity", "") for c in source_classifications], "uncertain"
            ),
            "policy_tools": tools,
            "target_segments": sorted({x for c in source_classifications for x in c.get("target_segments", [])}),
            "direct_target_evidence": "；".join(
                sorted({c.get("direct_target_evidence", "") for c in source_classifications if c.get("direct_target_evidence")})
            )[:200],
            "measure_or_guidance_evidence": "；".join(
                sorted(
                    {
                        c.get("measure_or_guidance_evidence", "")
                        for c in source_classifications
                        if c.get("measure_or_guidance_evidence")
                    }
                )
            )[:200],
            "specific_measures": sorted({x for c in source_classifications for x in c.get("specific_measures", [])})[:12],
            "eligibility_conditions": sorted({x for c in source_classifications for x in c.get("eligibility_conditions", [])})[:12],
            "implementation_mechanisms": sorted({x for c in source_classifications for x in c.get("implementation_mechanisms", [])})[:12],
            "strength_score": round(sum(strength_values) / len(strength_values)) if strength_values else 0,
            "coverage_breadth_score": round(sum(breadth_values) / len(breadth_values)) if breadth_values else 0,
        }
    )

    consensus = {
        "models_requested": [r["model"] for r in model_results],
        "models_succeeded": n,
        "models_failed": len(model_results) - n,
        "companies_succeeded": sorted({r["company"] for r in successful}),
        "nev_yes_votes": nev_yes,
        "industrial_yes_votes": industrial_yes,
        "policy_yes_votes": policy_yes,
        "policy_vote_share": policy_vote_share,
        "strict_majority_rule": "yes_votes > successful_models / 2; ties are rejected",
        "classification_disagreement": disagreement,
        "tool_jaccard_mean": tool_jaccard,
    }
    return merged, consensus


def parse_month(*values: Any) -> Tuple[Optional[str], str]:
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
    return None, "missing"


def load_prefecture_units() -> List[Dict[str, str]]:
    try:
        import cpca  # type: ignore
    except Exception:
        return []

    by_adcode = {info.adcode: info for info in cpca.ad_2_addr_dict.values()}
    provinces = {
        adcode: info.name
        for adcode, info in by_adcode.items()
        if getattr(info, "rank", None) == 0 and str(adcode).endswith("0000")
    }
    units: List[Dict[str, str]] = []
    seen = set()
    for adcode, info in by_adcode.items():
        code = str(adcode)
        if getattr(info, "rank", None) != 1:
            continue
        province = provinces.get(code[:2] + "0000", "")
        city = info.name
        if province in MUNICIPALITIES:
            city = province
            code = code[:2] + "0000"
        if city in {"市辖区", "县", "省直辖县级行政区划", "自治区直辖县级行政区划"}:
            continue
        key = (province, city, code[:6])
        if province and key not in seen:
            seen.add(key)
            units.append({"province": province, "city": city, "city_adcode": code[:6]})
    for province in MUNICIPALITIES:
        code = {"北京市": "110000", "天津市": "120000", "上海市": "310000", "重庆市": "500000"}[province]
        key = (province, province, code)
        if key not in seen:
            units.append({"province": province, "city": province, "city_adcode": code})
            seen.add(key)
    units.sort(key=lambda x: (x["province"], x["city_adcode"], x["city"]))
    return units


PREFECTURE_UNITS = load_prefecture_units()
PROVINCE_TO_CITIES: Dict[str, List[Dict[str, str]]] = defaultdict(list)
CITY_LOOKUP: Dict[str, Dict[str, str]] = {}
for unit in PREFECTURE_UNITS:
    PROVINCE_TO_CITIES[unit["province"]].append(unit)
    CITY_LOOKUP[f"{unit['province']}|{unit['city']}"] = unit


def infer_admin(record: Dict[str, Any]) -> Dict[str, str]:
    province_field = str(record.get("province") or "").strip()
    text = " ".join(
        str(record.get(k) or "")
        for k in ["pub_depart", "IssueDepartment_2", "IssueDepartment_3", "title", "province"]
    )
    admin = {"level": "unknown", "province": "", "city": "", "city_adcode": "", "source": "heuristic"}

    try:
        import cpca  # type: ignore

        df = cpca.transform([text])
        def clean_cell(value: Any) -> str:
            if value is None:
                return ""
            try:
                if value != value:
                    return ""
            except Exception:
                pass
            s = str(value).strip()
            return "" if s in {"", "nan", "NaN", "None"} else s

        parsed_prov = clean_cell(df.loc[0, "省"]) if "省" in df.columns else ""
        parsed_city = clean_cell(df.loc[0, "市"]) if "市" in df.columns else ""
        if parsed_prov in MUNICIPALITIES and not parsed_city:
            parsed_city = parsed_prov
        if parsed_prov and parsed_city:
            unit = CITY_LOOKUP.get(f"{parsed_prov}|{parsed_city}")
            admin.update(
                {
                    "level": "prefecture",
                    "province": parsed_prov,
                    "city": parsed_city,
                    "city_adcode": unit.get("city_adcode", "") if unit else "",
                    "source": "cpca",
                }
            )
            return admin
        if parsed_prov in ADMIN_PROVINCES:
            admin.update({"level": "province", "province": parsed_prov, "source": "cpca"})
            return admin
    except Exception:
        pass

    if province_field in MUNICIPALITIES:
        return {
            "level": "prefecture",
            "province": province_field,
            "city": province_field,
            "city_adcode": {"北京市": "110000", "天津市": "120000", "上海市": "310000", "重庆市": "500000"}[
                province_field
            ],
            "source": "province_field",
        }
    if province_field in ADMIN_PROVINCES:
        admin.update({"level": "province", "province": province_field, "source": "province_field"})
    elif any(marker in text for marker in CENTRAL_MARKERS) or province_field not in ADMIN_PROVINCES:
        admin.update({"level": "national", "source": "central_marker"})
    return admin


def compact_record(
    record: Dict[str, Any],
    text: str,
    score: int,
    hits: List[str],
    policy_hits: List[str],
    max_body_chars: int = 4500,
    long_doc_mode: str = "compress",
) -> Dict[str, Any]:
    month, precision = parse_month(record.get("pub_date"), record.get("use_date"), record.get("IssueDate"))
    llm_body, input_mode, source_len, input_len, compression_ratio = prepare_llm_body(
        text,
        hits or NEV_TERMS,
        argparse.Namespace(max_body_chars=max_body_chars, long_doc_mode=long_doc_mode),
    )
    return {
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
        "admin": infer_admin(record),
        "candidate_score": score,
        "nev_keyword_hits": hits,
        "policy_keyword_hits": policy_hits[:30],
        "text_char_len": len(text),
        "llm_body": llm_body,
        "llm_input_mode": input_mode,
        "llm_body_source_char_len": source_len,
        "llm_input_char_len": input_len,
        "llm_input_compression_ratio": round(compression_ratio, 4),
    }


def prompt_for_candidate(candidate: Dict[str, Any], args: Optional[argparse.Namespace] = None) -> str:
    template = user_prompt_template_for_args(args) if args is not None else USER_PROMPT_TEMPLATE
    return template.format(
        doc_id=candidate.get("id", ""),
        title=candidate.get("title", ""),
        province=candidate.get("province", ""),
        pub_depart=candidate.get("pub_depart", ""),
        law_type=candidate.get("law_type", ""),
        pub_date=candidate.get("pub_date", ""),
        use_date=candidate.get("use_date", ""),
        category_1=candidate.get("category_1", ""),
        category_2=candidate.get("category_2", ""),
        body=candidate.get("llm_body", ""),
    )


def read_done_ids(path: Path) -> set:
    done = set()
    if not path.exists():
        return done
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            done.add(str(item.get("id")))
    return done


def load_jsonl_records(path: Path) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    if not path.exists():
        return records
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                records.append(json.loads(line))
    return records


def count_jsonl_records(path: Path) -> int:
    with path.open("r", encoding="utf-8") as fh:
        return sum(1 for line in fh if line.strip())


def candidate_from_existing_candidate(row: Dict[str, Any], args: argparse.Namespace) -> Dict[str, Any]:
    body = norm_space(
        str(
            row.get("llm_body")
            or row.get("full_text")
            or row.get("body")
            or row.get("detail_flag")
            or row.get("body_snippet")
            or ""
        )
    )
    hits = canonical_list(row.get("nev_keyword_hits") or row.get("keyword_hits"))
    policy_hits = canonical_list(row.get("policy_keyword_hits"))
    month = row.get("date_month")
    precision = row.get("date_precision")
    if not month:
        month, precision = parse_month(row.get("pub_date"), row.get("use_date"), row.get("IssueDate"))
    admin = row.get("admin") if isinstance(row.get("admin"), dict) else infer_admin(row)
    llm_body, input_mode, source_len, input_len, compression_ratio = prepare_llm_body(
        body,
        hits or NEV_TERMS,
        args,
    )
    return {
        "id": row.get("id"),
        "law_db_name": row.get("law_db_name", ""),
        "title": row.get("title", ""),
        "detail_url": row.get("detail_url", ""),
        "province": row.get("province", ""),
        "EffectivenessDic": row.get("EffectivenessDic", ""),
        "TimelinessDic": row.get("TimelinessDic", ""),
        "IssueDate": row.get("IssueDate", ""),
        "IssueDepartment_2": row.get("IssueDepartment_2", ""),
        "IssueDepartment_3": row.get("IssueDepartment_3", ""),
        "category_1": row.get("category_1", ""),
        "category_2": row.get("category_2", ""),
        "law_type": row.get("law_type", ""),
        "pub_depart": row.get("pub_depart", ""),
        "pub_num": row.get("pub_num", ""),
        "pub_date": row.get("pub_date", ""),
        "use_date": row.get("use_date", ""),
        "is_time": row.get("is_time", ""),
        "date_month": month,
        "date_precision": precision or "missing",
        "admin": admin,
        "candidate_score": row.get("candidate_score", 0),
        "nev_keyword_hits": hits,
        "policy_keyword_hits": policy_hits[:30],
        "text_char_len": row.get("text_char_len", len(body)),
        "llm_body": llm_body,
        "llm_input_mode": input_mode,
        "llm_body_source_char_len": row.get("full_text_char_len") or row.get("llm_body_source_char_len") or source_len,
        "llm_input_char_len": input_len,
        "llm_input_compression_ratio": round(compression_ratio, 4),
    }


def normalize_existing_candidates_file(input_path: Path, output_path: Path, args: argparse.Namespace) -> int:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    count = 0
    with input_path.open("r", encoding="utf-8") as in_fh, tmp_path.open("w", encoding="utf-8") as out_fh:
        for line in in_fh:
            if not line.strip():
                continue
            row = json.loads(line)
            candidate = candidate_from_existing_candidate(row, args)
            out_fh.write(json.dumps(candidate, ensure_ascii=False) + "\n")
            count += 1
            if args.max_candidates and count >= args.max_candidates:
                break
    tmp_path.replace(output_path)
    print(f"Prepared normalized candidates: {output_path} ({count:,} records)", flush=True)
    return count


def candidate_from_record(record: Dict[str, Any], args: argparse.Namespace) -> Optional[Dict[str, Any]]:
    text = clean_text(record)
    title = str(record.get("title") or "")
    score, hits, policy_hits = candidate_score(title, text)
    if score < args.min_candidate_score:
        return None
    return compact_record(
        record,
        text,
        score,
        hits,
        policy_hits,
        max_body_chars=args.max_body_chars,
        long_doc_mode=getattr(args, "long_doc_mode", "compress"),
    )


def write_jsonl(path: Path, records: Sequence[Dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def collect_random_candidate_sample(
    input_path: Path,
    args: argparse.Namespace,
    candidates_path: Path,
) -> List[Dict[str, Any]]:
    rng = random.Random(args.sample_seed)
    sample_size = int(args.random_sample_candidates)
    sample: List[Dict[str, Any]] = []
    scanned = candidates = 0
    started = time.time()

    for record in iter_selected_records(input_path, args):
        scanned += 1
        if args.max_records and scanned > args.max_records:
            break
        candidate = candidate_from_record(record, args)
        if candidate is None:
            if scanned % args.progress_every == 0:
                print_progress(scanned, candidates, 0, 0, started)
            continue

        candidates += 1
        if len(sample) < sample_size:
            sample.append(candidate)
        else:
            pick = rng.randrange(candidates)
            if pick < sample_size:
                sample[pick] = candidate

        if scanned % args.progress_every == 0 or candidates % max(1, args.progress_every // 10) == 0:
            print_progress(scanned, candidates, 0, 0, started)

    write_jsonl(candidates_path, sample)
    print_progress(scanned, candidates, 0, 0, started, final=True)
    print(
        f"Random candidate sample: kept={len(sample):,} total_candidates={candidates:,} "
        f"seed={args.sample_seed} path={candidates_path}",
        flush=True,
    )
    return sample


def collect_latest_candidate_sample(
    input_path: Path,
    args: argparse.Namespace,
    candidates_path: Path,
) -> List[Dict[str, Any]]:
    sample_size = int(args.latest_candidates)
    sample = deque(maxlen=sample_size)
    scanned = candidates = 0
    started = time.time()

    for record in iter_selected_records(input_path, args):
        scanned += 1
        if args.max_records and scanned > args.max_records:
            break
        candidate = candidate_from_record(record, args)
        if candidate is None:
            if scanned % args.progress_every == 0:
                print_progress(scanned, candidates, 0, 0, started)
            continue

        candidates += 1
        sample.append(candidate)

        if scanned % args.progress_every == 0 or candidates % max(1, args.progress_every // 10) == 0:
            print_progress(scanned, candidates, 0, 0, started)
        if args.stop_after_candidates_filled and candidates >= sample_size:
            break

    out = list(sample)
    write_jsonl(candidates_path, out)
    print_progress(scanned, candidates, 0, 0, started, final=True)
    print(
        f"Latest candidate sample: kept={len(out):,} total_candidates={candidates:,} "
        f"path={candidates_path}",
        flush=True,
    )
    if len(out) < sample_size:
        print(
            f"WARNING: requested latest_candidates={sample_size:,}, but only found {len(out):,} candidates "
            "in the selected scan window.",
            flush=True,
        )
    return out


def no_llm_result(candidate: Dict[str, Any], models: Sequence[str]) -> Dict[str, Any]:
    return {
        **candidate,
        "classification": normalize_classification(
            {
                "is_nev_related": True,
                "is_industrial_policy": False,
                "confidence_is_nev_related": 0.5,
                "confidence_is_industrial_policy": 0.0,
                "classification_confidence": 0.0,
                "decision_reason": "LLM disabled; candidate requires model classification.",
            }
        ),
        "model_classifications": [],
        "adversarial_consensus": {
            "models_requested": list(models),
            "models_succeeded": 0,
            "models_failed": 0,
            "policy_yes_votes": 0,
            "policy_vote_share": 0.0,
            "classification_disagreement": False,
        },
        "llm_model": ",".join(models),
        "llm_error": "no_llm",
    }


def classify_one_candidate(
    candidate: Dict[str, Any],
    args: argparse.Namespace,
    models: Sequence[str],
) -> Dict[str, Any]:
    if args.no_llm:
        return no_llm_result(candidate, models)

    if getattr(args, "parallel_models", False) and len(models) > 1:
        by_model: Dict[str, Dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=len(models)) as pool:
            futures = {pool.submit(run_model_classification, candidate, args, model): model for model in models}
            for future in as_completed(futures):
                model = futures[future]
                try:
                    by_model[model] = future.result()
                except Exception as exc:
                    by_model[model] = {
                        "model": model,
                        "company": model_company(model),
                        "classification": normalize_classification(
                            {
                                "is_nev_related": False,
                                "is_industrial_policy": False,
                                "confidence_is_nev_related": 0.0,
                                "confidence_is_industrial_policy": 0.0,
                                "classification_confidence": 0.0,
                                "decision_reason": "LLM worker failed; this model is excluded from the ensemble vote.",
                            }
                        ),
                        "error": repr(exc)[:500],
                    }
        model_results = [by_model[model] for model in models]
    else:
        model_results = [run_model_classification(candidate, args, model) for model in models]
    final_cls, consensus = aggregate_model_classifications(model_results)
    return {
        **candidate,
        "classification": final_cls,
        "model_classifications": model_results,
        "adversarial_consensus": consensus,
        "classification_run_mode": "three_model_independent_vote",
        "prompt_mode": prompt_mode(args),
        "parallel_models": bool(getattr(args, "parallel_models", False)),
        "parallel_docs": int(getattr(args, "parallel_docs", 1) or 1),
        "llm_model": ",".join(models),
        "llm_error": " | ".join(f"{r['model']}: {r['error']}" for r in model_results if r.get("error"))[:1000],
    }


def empty_vote_stats() -> Dict[str, int]:
    return {
        "rows": 0,
        "non_unanimous_vote_rows": 0,
        "all_yes_vote_rows": 0,
        "all_no_vote_rows": 0,
        "all_failed_rows": 0,
    }


def is_non_unanimous_policy_vote(item: Dict[str, Any]) -> bool:
    consensus = item.get("adversarial_consensus") or {}
    try:
        share = float(consensus.get("policy_vote_share"))
    except Exception:
        return False
    return 0.0 < share < 1.0


def update_vote_stats(stats: Dict[str, int], item: Dict[str, Any]) -> None:
    consensus = item.get("adversarial_consensus") or {}
    stats["rows"] += 1
    succeeded = int(consensus.get("models_succeeded") or 0)
    try:
        share = float(consensus.get("policy_vote_share"))
    except Exception:
        share = -1.0
    if succeeded <= 0:
        stats["all_failed_rows"] += 1
    elif 0.0 < share < 1.0:
        stats["non_unanimous_vote_rows"] += 1
    elif share >= 1.0:
        stats["all_yes_vote_rows"] += 1
    elif share == 0.0:
        stats["all_no_vote_rows"] += 1


def read_vote_stats(path: Path) -> Dict[str, int]:
    stats = empty_vote_stats()
    if not path.exists():
        return stats
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                update_vote_stats(stats, json.loads(line))
            except json.JSONDecodeError:
                continue
    return stats


def format_duration(seconds: Optional[float]) -> str:
    if seconds is None or seconds < 0 or math.isinf(seconds) or math.isnan(seconds):
        return "unknown"
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days:
        return f"{days}d{hours:02d}h"
    if hours:
        return f"{hours}h{minutes:02d}m"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"


def print_classify_status(
    processed: int,
    total: int,
    newly_classified: int,
    skipped_done: int,
    stats: Dict[str, int],
    started: float,
    final: bool = False,
) -> None:
    elapsed = max(0.1, time.time() - started)
    rate = newly_classified / elapsed if newly_classified else 0.0
    remaining = max(0, total - stats["rows"])
    eta = remaining / rate if rate > 0 else None
    ratio = stats["non_unanimous_vote_rows"] / stats["rows"] if stats["rows"] else 0.0
    tag = "CLASSIFY FINAL" if final else "CLASSIFY"
    print(
        f"[{tag}] processed={processed:,}/{total:,} "
        f"newly_classified={newly_classified:,} resume_skips={skipped_done:,} "
        f"total_classified={stats['rows']:,}/{total:,} remaining={remaining:,} "
        f"new_rate={rate*60:.2f}/min ETA={format_duration(eta)} "
        f"non_unanimous={stats['non_unanimous_vote_rows']:,}/{stats['rows']:,} ({ratio:.2%}) "
        f"all_yes={stats['all_yes_vote_rows']:,} all_no={stats['all_no_vote_rows']:,} "
        f"all_failed={stats['all_failed_rows']:,}",
        flush=True,
    )


def classify_candidate_sample(
    sample: Sequence[Dict[str, Any]],
    args: argparse.Namespace,
    models: Sequence[str],
    classified_path: Path,
    done_ids: set,
    append: bool,
) -> None:
    classified = skipped_done = 0
    started = time.time()
    mode = "a" if append else "w"
    progress_every = max(1, min(args.progress_every, 25))

    with classified_path.open(mode, encoding="utf-8") as cls_fh:
        for idx, candidate in enumerate(sample, start=1):
            doc_id = str(candidate.get("id"))
            if doc_id in done_ids:
                skipped_done += 1
                continue

            result = classify_one_candidate(candidate, args, models)
            cls_fh.write(json.dumps(result, ensure_ascii=False) + "\n")
            cls_fh.flush()
            classified += 1

            if idx % progress_every == 0 or idx == len(sample):
                elapsed = max(0.1, time.time() - started)
                print(
                    f"[CLASSIFY] sampled={idx:,}/{len(sample):,} classified={classified:,} "
                    f"resume_skips={skipped_done:,} rate={idx/elapsed:,.2f}/s elapsed={elapsed/60:.1f}m",
                    flush=True,
                )

    elapsed = max(0.1, time.time() - started)
    print(
        f"[CLASSIFY FINAL] sampled={len(sample):,} classified={classified:,} "
        f"resume_skips={skipped_done:,} elapsed={elapsed/60:.1f}m",
        flush=True,
    )
    print(f"Classified: {classified_path}", flush=True)


def classify_candidate_file(
    candidates_path: Path,
    args: argparse.Namespace,
    models: Sequence[str],
    classified_path: Path,
    done_ids: set,
    append: bool,
) -> None:
    total_available = count_jsonl_records(candidates_path)
    total = min(total_available, args.max_candidates) if args.max_candidates else total_available
    classified = skipped_done = processed = 0
    started = time.time()
    mode = "a" if append else "w"
    progress_every = max(1, args.progress_every)
    stats = read_vote_stats(classified_path) if append else empty_vote_stats()
    parallel_docs = max(1, int(getattr(args, "parallel_docs", 1) or 1))

    with candidates_path.open("r", encoding="utf-8") as cand_fh, classified_path.open(mode, encoding="utf-8") as cls_fh:
        if parallel_docs > 1 and not args.no_llm:
            futures: Dict[Any, str] = {}

            def drain_ready(block: bool = False) -> None:
                nonlocal classified
                if not futures:
                    return
                done, _pending = wait(
                    futures.keys(),
                    return_when=FIRST_COMPLETED if block else FIRST_COMPLETED,
                    timeout=None if block else 0,
                )
                for future in done:
                    futures.pop(future, None)
                    result = future.result()
                    cls_fh.write(json.dumps(result, ensure_ascii=False) + "\n")
                    cls_fh.flush()
                    classified += 1
                    update_vote_stats(stats, result)
                    if classified % progress_every == 0 or stats["rows"] == total:
                        print_classify_status(processed, total, classified, skipped_done, stats, started)

            with ThreadPoolExecutor(max_workers=parallel_docs) as pool:
                for line in cand_fh:
                    if not line.strip():
                        continue
                    if args.max_candidates and processed >= args.max_candidates:
                        break
                    processed += 1
                    candidate = json.loads(line)
                    doc_id = str(candidate.get("id"))
                    if doc_id in done_ids:
                        skipped_done += 1
                        if processed % progress_every == 0:
                            print_classify_status(processed, total, classified, skipped_done, stats, started)
                        continue

                    while len(futures) >= parallel_docs:
                        drain_ready(block=True)
                    futures[pool.submit(classify_one_candidate, candidate, args, models)] = doc_id
                    drain_ready(block=False)

                while futures:
                    drain_ready(block=True)

            print_classify_status(processed, total, classified, skipped_done, stats, started, final=True)
            print(f"Classified: {classified_path}", flush=True)
            return

        for line in cand_fh:
            if not line.strip():
                continue
            if args.max_candidates and processed >= args.max_candidates:
                break
            processed += 1
            candidate = json.loads(line)
            doc_id = str(candidate.get("id"))
            if doc_id in done_ids:
                skipped_done += 1
                if processed % progress_every == 0:
                    print_classify_status(processed, total, classified, skipped_done, stats, started)
                continue

            result = classify_one_candidate(candidate, args, models)
            cls_fh.write(json.dumps(result, ensure_ascii=False) + "\n")
            cls_fh.flush()
            classified += 1
            update_vote_stats(stats, result)

            if processed % progress_every == 0 or processed == total:
                print_classify_status(processed, total, classified, skipped_done, stats, started)

    print_classify_status(processed, total, classified, skipped_done, stats, started, final=True)
    print(f"Classified: {classified_path}", flush=True)


def classify_command(args: argparse.Namespace) -> None:
    input_path = Path(args.input)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    candidates_path = out_dir / args.candidates_name
    classified_path = out_dir / args.classified_name
    models = parse_model_list(args.models, args.model)

    done_ids = read_done_ids(classified_path) if args.resume else set()
    mode = "a" if args.resume else "w"

    if args.random_sample_candidates and args.latest_candidates:
        raise SystemExit("Use either --random-sample-candidates or --latest-candidates, not both.")

    if args.existing_candidates:
        existing_candidates_path = Path(args.existing_candidates)
        if not existing_candidates_path.exists():
            raise SystemExit(f"Existing candidates file not found: {existing_candidates_path}")
        regenerate_candidates = (not args.resume) or (not candidates_path.exists())
        if args.resume and candidates_path.exists() and args.max_candidates:
            existing_count = count_jsonl_records(candidates_path)
            if existing_count < args.max_candidates:
                print(
                    f"Regenerating normalized candidates for expanded resume target: "
                    f"{existing_count:,} -> {args.max_candidates:,}",
                    flush=True,
                )
                regenerate_candidates = True
        if regenerate_candidates:
            normalize_existing_candidates_file(existing_candidates_path, candidates_path, args)
        else:
            print(
                f"Loaded normalized candidates for resume: {candidates_path} "
                f"({count_jsonl_records(candidates_path):,} records)",
                flush=True,
            )
        if args.candidates_only:
            print(f"Candidates-only mode; skipping LLM classification: {candidates_path}", flush=True)
            return
        classify_candidate_file(candidates_path, args, models, classified_path, done_ids, append=args.resume)
        print(f"Candidates: {candidates_path}")
        print(f"Classified: {classified_path}")
        return

    if args.latest_candidates:
        if args.resume and candidates_path.exists():
            sample = load_jsonl_records(candidates_path)
            print(f"Loaded existing latest candidate sample: {candidates_path} ({len(sample):,} records)", flush=True)
        else:
            sample = collect_latest_candidate_sample(input_path, args, candidates_path)
        if args.candidates_only:
            print(f"Candidates-only mode; skipping LLM classification: {candidates_path}", flush=True)
            return
        classify_candidate_sample(sample, args, models, classified_path, done_ids, append=args.resume)
        print(f"Candidates: {candidates_path}")
        print(f"Classified: {classified_path}")
        return

    if args.random_sample_candidates:
        if args.resume and candidates_path.exists():
            sample = load_jsonl_records(candidates_path)
            print(f"Loaded existing random candidate sample: {candidates_path} ({len(sample):,} records)", flush=True)
        else:
            sample = collect_random_candidate_sample(input_path, args, candidates_path)
        if args.candidates_only:
            print(f"Candidates-only mode; skipping LLM classification: {candidates_path}", flush=True)
            return
        classify_candidate_sample(sample, args, models, classified_path, done_ids, append=args.resume)
        print(f"Candidates: {candidates_path}")
        print(f"Classified: {classified_path}")
        return

    scanned = candidates = classified = skipped_done = 0
    started = time.time()
    with candidates_path.open(mode, encoding="utf-8") as cand_fh, classified_path.open(mode, encoding="utf-8") as cls_fh:
        for record in iter_selected_records(input_path, args):
            scanned += 1
            if args.max_records and scanned > args.max_records:
                break
            candidate = candidate_from_record(record, args)
            if candidate is None:
                if scanned % args.progress_every == 0:
                    print_progress(scanned, candidates, classified, skipped_done, started)
                continue

            candidates += 1
            cand_fh.write(json.dumps(candidate, ensure_ascii=False) + "\n")
            cand_fh.flush()

            doc_id = str(candidate.get("id"))
            if doc_id in done_ids:
                skipped_done += 1
                continue
            result = classify_one_candidate(candidate, args, models)

            cls_fh.write(json.dumps(result, ensure_ascii=False) + "\n")
            cls_fh.flush()
            classified += 1

            if args.max_candidates and candidates >= args.max_candidates:
                break
            if scanned % args.progress_every == 0 or candidates % max(1, args.progress_every // 10) == 0:
                print_progress(scanned, candidates, classified, skipped_done, started)

    print_progress(scanned, candidates, classified, skipped_done, started, final=True)
    print(f"Candidates: {candidates_path}")
    print(f"Classified: {classified_path}")


def print_progress(
    scanned: int, candidates: int, classified: int, skipped_done: int, started: float, final: bool = False
) -> None:
    elapsed = max(0.1, time.time() - started)
    rate = scanned / elapsed
    tag = "FINAL" if final else "PROGRESS"
    print(
        f"[{tag}] scanned={scanned:,} candidates={candidates:,} classified={classified:,} "
        f"resume_skips={skipped_done:,} rate={rate:,.1f}/s elapsed={elapsed/60:.1f}m",
        flush=True,
    )


def month_iter(start: str, end: str) -> Iterator[str]:
    y, m = map(int, start.split("-"))
    ey, em = map(int, end.split("-"))
    while (y, m) <= (ey, em):
        yield f"{y:04d}-{m:02d}"
        m += 1
        if m == 13:
            y += 1
            m = 1


def affected_cities(admin: Dict[str, str]) -> List[Dict[str, str]]:
    level = admin.get("level", "unknown")
    province = admin.get("province", "")
    city = admin.get("city", "")
    if level == "national":
        return PREFECTURE_UNITS
    if level == "province" and province:
        return PROVINCE_TO_CITIES.get(province, [])
    if city:
        unit = CITY_LOOKUP.get(f"{province}|{city}")
        if unit:
            return [unit]
        return [{"province": province, "city": city, "city_adcode": admin.get("city_adcode", "")}]
    if province:
        return PROVINCE_TO_CITIES.get(province, [])
    return []


def confidence_weight(cls: Dict[str, Any]) -> float:
    vals = [
        safe_float(cls.get("confidence_is_nev_related"), 0.0),
        safe_float(cls.get("confidence_is_industrial_policy"), 0.0),
        safe_float(cls.get("classification_confidence"), 0.0),
    ]
    vals = [v for v in vals if v > 0]
    return min(vals) if vals else 0.0


def panel_command(args: argparse.Namespace) -> None:
    classified_path = Path(args.classified)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    docs_csv = out_dir / args.documents_csv
    expanded_csv = out_dir / args.expanded_csv
    panel_csv = out_dir / args.panel_csv
    central_panel_csv = out_dir / args.central_panel_csv
    province_panel_csv = out_dir / args.province_panel_csv
    prefecture_panel_csv = out_dir / args.prefecture_panel_csv
    summary_json = out_dir / args.summary_json

    docs: List[Dict[str, Any]] = []
    with classified_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            cls = row.get("classification", {})
            conf = confidence_weight(cls)
            if not (cls.get("is_nev_related") and cls.get("is_industrial_policy")):
                continue
            if conf < args.min_confidence:
                continue
            if not row.get("date_month"):
                continue
            docs.append(row)

    if not docs:
        raise SystemExit("No NEV industrial policy documents passed the panel filter.")

    months = [d["date_month"] for d in docs if d.get("date_month")]
    start_month = args.start_month or min(months)
    end_month = args.end_month or max(months)

    doc_fields = [
        "id",
        "title",
        "province",
        "pub_depart",
        "law_type",
        "pub_num",
        "pub_date",
        "use_date",
        "date_month",
        "date_precision",
        "admin_level",
        "admin_province",
        "admin_city",
        "candidate_score",
        "confidence_weight",
        "models_requested",
        "models_succeeded",
        "models_failed",
        "companies_succeeded",
        "policy_yes_votes",
        "policy_vote_share",
        "classification_disagreement",
        "tool_jaccard_mean",
        "policy_tone",
        "timing",
        "policy_side",
        "measure_specificity",
        "direct_target_evidence",
        "measure_or_guidance_evidence",
        "strength_score",
        "coverage_breadth_score",
        "policy_tools",
        "tool_groups",
        "target_segments",
        "specific_measures",
        "eligibility_conditions",
        "implementation_mechanisms",
        "false_positive_risk",
        "adversarial_not_policy_case",
        "decision_reason",
        "llm_model",
        "llm_error",
    ]

    with docs_csv.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=doc_fields)
        writer.writeheader()
        for d in docs:
            cls = d["classification"]
            admin = d.get("admin", {})
            consensus = d.get("adversarial_consensus", {})
            writer.writerow(
                {
                    "id": d.get("id"),
                    "title": d.get("title"),
                    "province": d.get("province"),
                    "pub_depart": d.get("pub_depart"),
                    "law_type": d.get("law_type"),
                    "pub_num": d.get("pub_num"),
                    "pub_date": d.get("pub_date"),
                    "use_date": d.get("use_date"),
                    "date_month": d.get("date_month"),
                    "date_precision": d.get("date_precision"),
                    "admin_level": admin.get("level"),
                    "admin_province": admin.get("province"),
                    "admin_city": admin.get("city"),
                    "candidate_score": d.get("candidate_score"),
                    "confidence_weight": confidence_weight(cls),
                    "models_requested": "|".join(consensus.get("models_requested", [])),
                    "models_succeeded": consensus.get("models_succeeded", ""),
                    "models_failed": consensus.get("models_failed", ""),
                    "companies_succeeded": "|".join(consensus.get("companies_succeeded", [])),
                    "policy_yes_votes": consensus.get("policy_yes_votes", ""),
                    "policy_vote_share": consensus.get("policy_vote_share", ""),
                    "classification_disagreement": consensus.get("classification_disagreement", ""),
                    "tool_jaccard_mean": consensus.get("tool_jaccard_mean", ""),
                    "policy_tone": cls.get("policy_tone"),
                    "timing": cls.get("timing"),
                    "policy_side": cls.get("policy_side"),
                    "measure_specificity": cls.get("measure_specificity"),
                    "direct_target_evidence": cls.get("direct_target_evidence"),
                    "measure_or_guidance_evidence": cls.get("measure_or_guidance_evidence"),
                    "strength_score": cls.get("strength_score"),
                    "coverage_breadth_score": cls.get("coverage_breadth_score"),
                    "policy_tools": "|".join(cls.get("policy_tools", [])),
                    "tool_groups": "|".join(cls.get("tool_groups", [])),
                    "target_segments": "|".join(cls.get("target_segments", [])),
                    "specific_measures": "|".join(cls.get("specific_measures", [])),
                    "eligibility_conditions": "|".join(cls.get("eligibility_conditions", [])),
                    "implementation_mechanisms": "|".join(cls.get("implementation_mechanisms", [])),
                    "false_positive_risk": cls.get("false_positive_risk"),
                    "adversarial_not_policy_case": cls.get("adversarial_not_policy_case"),
                    "decision_reason": cls.get("decision_reason"),
                    "llm_model": d.get("llm_model"),
                    "llm_error": d.get("llm_error"),
                }
            )

    def source_panel_base(scope: str, province: str, city: str, city_adcode: str, month: str) -> Dict[str, Any]:
        row: Dict[str, Any] = {
            "scope": scope,
            "province": province,
            "city": city,
            "city_adcode": city_adcode,
            "year_month": month,
            "policy_count": 0,
            "confidence_weighted_count": 0.0,
            "strength_sum": 0.0,
            "strength_mean": 0.0,
            "coverage_breadth_sum": 0.0,
            "coverage_breadth_mean": 0.0,
            "tool_breadth_sum": 0,
            "tool_breadth_mean": 0.0,
            "tool_groups_unique_count": 0,
            "target_segments_unique_count": 0,
            "non_unanimous_vote_count": 0,
            "low_confidence_count": 0,
            "guidance_only_count": 0,
            "specific_or_mixed_measure_count": 0,
        }
        for tone in TONES:
            row[f"tone_{tone}_count"] = 0
        for timing in TIMINGS:
            row[f"timing_{timing}_count"] = 0
        for side in SIDES:
            row[f"side_{side}_count"] = 0
        for specificity in MEASURE_SPECIFICITIES:
            row[f"measure_{specificity}_count"] = 0
        for group in TOOL_GROUPS:
            row[f"group_{group}_count"] = 0
        for tool in TOOL_DEFS:
            row[f"tool_{tool}_count"] = 0
        row["_tool_groups"] = set()
        row["_target_segments"] = set()
        return row

    def add_doc_to_source_panel(row: Dict[str, Any], d: Dict[str, Any]) -> None:
        cls = d["classification"]
        consensus = d.get("adversarial_consensus", {})
        conf = confidence_weight(cls)
        tools = cls.get("policy_tools", [])
        groups = cls.get("tool_groups", [])
        target_segments = cls.get("target_segments", [])
        row["policy_count"] += 1
        row["confidence_weighted_count"] += conf
        row["strength_sum"] += cls.get("strength_score", 0) * conf
        row["coverage_breadth_sum"] += cls.get("coverage_breadth_score", 0) * conf
        row["tool_breadth_sum"] += len(tools)
        try:
            vote_share = float(consensus.get("policy_vote_share"))
        except Exception:
            vote_share = 0.0
        if 0.0 < vote_share < 1.0:
            row["non_unanimous_vote_count"] += 1
        if conf < 0.65:
            row["low_confidence_count"] += 1
        measure_specificity = cls.get("measure_specificity", "uncertain")
        if measure_specificity == "guidance_only":
            row["guidance_only_count"] += 1
        elif measure_specificity in {"specific_measures", "mixed"}:
            row["specific_or_mixed_measure_count"] += 1
        tone = cls.get("policy_tone", "uncertain")
        timing = cls.get("timing", "uncertain")
        side = cls.get("policy_side", "uncertain")
        row[f"tone_{tone if tone in TONES else 'uncertain'}_count"] += 1
        row[f"timing_{timing if timing in TIMINGS else 'uncertain'}_count"] += 1
        row[f"side_{side if side in SIDES else 'uncertain'}_count"] += 1
        row[f"measure_{measure_specificity if measure_specificity in MEASURE_SPECIFICITIES else 'uncertain'}_count"] += 1
        for group in groups:
            if group in TOOL_GROUPS:
                row[f"group_{group}_count"] += 1
                row["_tool_groups"].add(group)
        for tool in tools:
            if tool in TOOL_DEFS:
                row[f"tool_{tool}_count"] += 1
        for seg in target_segments:
            row["_target_segments"].add(seg)

    def write_source_panel(
        path: Path,
        scope: str,
        units: Sequence[Dict[str, str]],
        rows: Dict[Tuple[str, str, str, str], Dict[str, Any]],
    ) -> int:
        if units:
            fields = list(
                source_panel_base(
                    scope,
                    units[0].get("province", ""),
                    units[0].get("city", ""),
                    units[0].get("city_adcode", ""),
                    start_month,
                ).keys()
            )
        else:
            fields = list(source_panel_base(scope, "", "", "", start_month).keys())
        fields = [field for field in fields if not field.startswith("_")]
        written = 0
        with path.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields)
            writer.writeheader()
            for unit in units:
                for month in month_iter(start_month, end_month):
                    key = (scope, unit.get("province", ""), unit.get("city", ""), month)
                    row = rows.get(
                        key,
                        source_panel_base(
                            scope,
                            unit.get("province", ""),
                            unit.get("city", ""),
                            unit.get("city_adcode", ""),
                            month,
                        ),
                    )
                    count = row["policy_count"]
                    conf_count = row["confidence_weighted_count"]
                    if conf_count:
                        row["strength_mean"] = row["strength_sum"] / conf_count
                        row["coverage_breadth_mean"] = row["coverage_breadth_sum"] / conf_count
                    if count:
                        row["tool_breadth_mean"] = row["tool_breadth_sum"] / count
                    row["tool_groups_unique_count"] = len(row["_tool_groups"])
                    row["target_segments_unique_count"] = len(row["_target_segments"])
                    writer.writerow({k: row.get(k, "") for k in fields})
                    written += 1
        return written

    central_units = [{"province": "", "city": "", "city_adcode": ""}]
    province_units = [{"province": province, "city": "", "city_adcode": ""} for province in sorted(ADMIN_PROVINCES)]
    prefecture_units = PREFECTURE_UNITS
    source_panel_rows: Dict[Tuple[str, str, str, str], Dict[str, Any]] = {}

    for d in docs:
        admin = d.get("admin", {})
        level = admin.get("level", "unknown")
        month = d["date_month"]
        if level == "national":
            scope = "central"
            unit = {"province": "", "city": "", "city_adcode": ""}
        elif level == "province":
            scope = "province"
            unit = {"province": admin.get("province", ""), "city": "", "city_adcode": ""}
        elif level == "prefecture":
            scope = "prefecture"
            unit = {
                "province": admin.get("province", ""),
                "city": admin.get("city", ""),
                "city_adcode": admin.get("city_adcode", ""),
            }
        else:
            continue
        key = (scope, unit.get("province", ""), unit.get("city", ""), month)
        if key not in source_panel_rows:
            source_panel_rows[key] = source_panel_base(
                scope,
                unit.get("province", ""),
                unit.get("city", ""),
                unit.get("city_adcode", ""),
                month,
            )
        add_doc_to_source_panel(source_panel_rows[key], d)

    central_panel_rows = write_source_panel(central_panel_csv, "central", central_units, source_panel_rows)
    province_panel_rows = write_source_panel(province_panel_csv, "province", province_units, source_panel_rows)
    prefecture_panel_rows = write_source_panel(prefecture_panel_csv, "prefecture", prefecture_units, source_panel_rows)

    panel: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    expanded_rows = 0

    def base_row(unit: Dict[str, str], month: str) -> Dict[str, Any]:
        row: Dict[str, Any] = {
            "province": unit["province"],
            "city": unit["city"],
            "city_adcode": unit.get("city_adcode", ""),
            "year_month": month,
            "policy_count": 0,
            "confidence_weighted_count": 0.0,
            "strength_sum": 0.0,
            "strength_mean": 0.0,
            "coverage_breadth_sum": 0.0,
            "coverage_breadth_mean": 0.0,
            "tool_breadth_sum": 0,
            "tool_breadth_mean": 0.0,
            "tool_groups_unique_count": 0,
            "target_segments_unique_count": 0,
            "national_policy_count": 0,
            "provincial_policy_count": 0,
            "local_policy_count": 0,
        }
        for tone in TONES:
            row[f"tone_{tone}_count"] = 0
        for timing in TIMINGS:
            row[f"timing_{timing}_count"] = 0
        for side in SIDES:
            row[f"side_{side}_count"] = 0
        for specificity in MEASURE_SPECIFICITIES:
            row[f"measure_{specificity}_count"] = 0
        for group in TOOL_GROUPS:
            row[f"group_{group}_count"] = 0
        for tool in TOOL_DEFS:
            row[f"tool_{tool}_count"] = 0
        row["_tool_groups"] = set()
        row["_target_segments"] = set()
        return row

    with expanded_csv.open("w", encoding="utf-8-sig", newline="") as fh:
        fieldnames = [
            "id",
            "title",
            "source_level",
            "source_province",
            "source_city",
            "affected_province",
            "affected_city",
            "affected_city_adcode",
            "year_month",
            "confidence_weight",
            "strength_score",
            "coverage_breadth_score",
            "policy_tone",
            "timing",
            "policy_side",
            "measure_specificity",
            "policy_tools",
            "tool_groups",
            "target_segments",
        ]
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for d in docs:
            cls = d["classification"]
            admin = d.get("admin", {})
            month = d["date_month"]
            conf = confidence_weight(cls)
            tools = cls.get("policy_tools", [])
            groups = cls.get("tool_groups", [])
            target_segments = cls.get("target_segments", [])
            for unit in affected_cities(admin):
                key = (unit["province"], unit["city"], month)
                if key not in panel:
                    panel[key] = base_row(unit, month)
                row = panel[key]
                row["policy_count"] += 1
                row["confidence_weighted_count"] += conf
                row["strength_sum"] += cls.get("strength_score", 0) * conf
                row["coverage_breadth_sum"] += cls.get("coverage_breadth_score", 0) * conf
                row["tool_breadth_sum"] += len(tools)
                level = admin.get("level", "unknown")
                if level == "national":
                    row["national_policy_count"] += 1
                elif level == "province":
                    row["provincial_policy_count"] += 1
                else:
                    row["local_policy_count"] += 1
                tone = cls.get("policy_tone", "uncertain")
                timing = cls.get("timing", "uncertain")
                side = cls.get("policy_side", "uncertain")
                measure_specificity = cls.get("measure_specificity", "uncertain")
                row[f"tone_{tone if tone in TONES else 'uncertain'}_count"] += 1
                row[f"timing_{timing if timing in TIMINGS else 'uncertain'}_count"] += 1
                row[f"side_{side if side in SIDES else 'uncertain'}_count"] += 1
                row[
                    f"measure_{measure_specificity if measure_specificity in MEASURE_SPECIFICITIES else 'uncertain'}_count"
                ] += 1
                for group in groups:
                    if group in TOOL_GROUPS:
                        row[f"group_{group}_count"] += 1
                        row["_tool_groups"].add(group)
                for tool in tools:
                    if tool in TOOL_DEFS:
                        row[f"tool_{tool}_count"] += 1
                for seg in target_segments:
                    row["_target_segments"].add(seg)

                writer.writerow(
                    {
                        "id": d.get("id"),
                        "title": d.get("title"),
                        "source_level": level,
                        "source_province": admin.get("province"),
                        "source_city": admin.get("city"),
                        "affected_province": unit["province"],
                        "affected_city": unit["city"],
                        "affected_city_adcode": unit.get("city_adcode", ""),
                        "year_month": month,
                        "confidence_weight": conf,
                        "strength_score": cls.get("strength_score"),
                        "coverage_breadth_score": cls.get("coverage_breadth_score"),
                        "policy_tone": cls.get("policy_tone"),
                        "timing": cls.get("timing"),
                        "policy_side": cls.get("policy_side"),
                        "measure_specificity": cls.get("measure_specificity"),
                        "policy_tools": "|".join(tools),
                        "tool_groups": "|".join(groups),
                        "target_segments": "|".join(target_segments),
                    }
                )
                expanded_rows += 1

    all_units = PREFECTURE_UNITS
    panel_template_unit = all_units[0] if all_units else {"province": "", "city": "", "city_adcode": ""}
    panel_fields = list(base_row(panel_template_unit, start_month).keys())
    panel_fields = [f for f in panel_fields if not f.startswith("_")]

    with panel_csv.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=panel_fields)
        writer.writeheader()
        for unit in all_units:
            for month in month_iter(start_month, end_month):
                row = panel.get((unit["province"], unit["city"], month), base_row(unit, month))
                count = row["policy_count"]
                conf_count = row["confidence_weighted_count"]
                if conf_count:
                    row["strength_mean"] = row["strength_sum"] / conf_count
                    row["coverage_breadth_mean"] = row["coverage_breadth_sum"] / conf_count
                if count:
                    row["tool_breadth_mean"] = row["tool_breadth_sum"] / count
                row["tool_groups_unique_count"] = len(row["_tool_groups"])
                row["target_segments_unique_count"] = len(row["_target_segments"])
                writer.writerow({k: row.get(k, "") for k in panel_fields})

    summary = {
        "classified_input": str(classified_path),
        "documents_kept": len(docs),
        "expanded_city_policy_rows": expanded_rows,
        "prefecture_units": len(PREFECTURE_UNITS),
        "start_month": start_month,
        "end_month": end_month,
        "min_confidence": args.min_confidence,
        "outputs": {
            "documents_csv": str(docs_csv),
            "central_panel_csv": str(central_panel_csv),
            "province_panel_csv": str(province_panel_csv),
            "prefecture_panel_csv": str(prefecture_panel_csv),
            "expanded_csv": str(expanded_csv),
            "panel_csv": str(panel_csv),
        },
        "source_panel_rows": {
            "central": central_panel_rows,
            "province": province_panel_rows,
            "prefecture": prefecture_panel_rows,
        },
        "tool_definitions": TOOL_DEFS,
    }
    summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_classify = sub.add_parser(
        "classify", help=f"Stream input JSON and classify {DOMAIN_LABEL} policy candidates."
    )
    p_classify.add_argument("--input", required=True)
    p_classify.add_argument("--output-dir", default="outputs/nev_policy_panel")
    p_classify.add_argument("--json-prefix", default="item")
    p_classify.add_argument("--candidates-name", default="nev_candidates.jsonl")
    p_classify.add_argument("--classified-name", default="nev_classified.jsonl")
    p_classify.add_argument(
        "--existing-candidates",
        default="",
        help=f"Use an already screened {DOMAIN_LABEL} candidate JSONL instead of scanning the source JSON again.",
    )
    p_classify.add_argument("--model", default="qwen3:4b-mirror")
    p_classify.add_argument(
        "--models",
        default="",
        help="Comma-separated Ollama models for adversarial ensemble, e.g. gemma3:4b-mirror,qwen3:4b-mirror,llama3.2:3b-mirror",
    )
    p_classify.add_argument(
        "--prompt-mode",
        choices=["standard", "adversarial", "boundary_vote"],
        default="standard",
        help="standard = independent structured coding; adversarial = include explicit strongest-not-policy reasoning; boundary_vote = compact second-vote yes/no prompt.",
    )
    p_classify.add_argument(
        "--parallel-models",
        action="store_true",
        help="Call the listed models concurrently for each document. Recommended for Ollama Pro cloud; avoid on low-memory local runs.",
    )
    p_classify.add_argument(
        "--parallel-docs",
        type=int,
        default=1,
        help="Classify this many documents concurrently. Use on cloud/API-backed runs; keep 1 for low-memory local Ollama.",
    )
    p_classify.add_argument("--ollama-host", default=os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434"))
    p_classify.add_argument(
        "--ollama-format",
        choices=["auto", "json", "empty", "none"],
        default="auto",
        help="Ollama generate format setting. auto uses empty format for gpt-oss and json for other models.",
    )
    p_classify.add_argument("--llm-timeout", type=int, default=180)
    p_classify.add_argument("--llm-retries", type=int, default=2)
    p_classify.add_argument("--retry-base-sleep", type=float, default=4.0)
    p_classify.add_argument("--num-ctx", type=int, default=8192)
    p_classify.add_argument("--max-body-chars", type=int, default=4500)
    p_classify.add_argument(
        "--long-doc-mode",
        choices=["compress", "window", "truncate", "evidence_pack"],
        default="compress",
        help=(
            "How to fit documents longer than --max-body-chars. "
            "compress keeps policy-evidence paragraphs only for overlong docs; evidence_pack deterministically "
            "extracts policy evidence for all docs; window uses keyword windows; truncate keeps the prefix."
        ),
    )
    p_classify.add_argument("--min-candidate-score", type=int, default=3)
    p_classify.add_argument("--max-records", type=int, default=0)
    p_classify.add_argument(
        "--last-records",
        type=int,
        default=0,
        help="Only scan the last N records of a pretty-printed JSON array input.",
    )
    p_classify.add_argument("--max-candidates", type=int, default=0)
    p_classify.add_argument(
        "--latest-candidates",
        type=int,
        default=0,
        help="After scanning the selected records, keep the latest N NEV candidates for LLM classification.",
    )
    p_classify.add_argument(
        "--stop-after-candidates-filled",
        action="store_true",
        help="With --latest-candidates, stop scanning once the requested number of NEV candidates has been found.",
    )
    p_classify.add_argument(
        "--random-sample-candidates",
        type=int,
        default=0,
        help="Reservoir-sample this many BeautifulSoup/keyword NEV candidates before LLM classification.",
    )
    p_classify.add_argument("--sample-seed", type=int, default=20260604)
    p_classify.add_argument("--progress-every", type=int, default=1000)
    p_classify.add_argument("--resume", action="store_true")
    p_classify.add_argument("--no-llm", action="store_true")
    p_classify.add_argument("--candidates-only", action="store_true")
    p_classify.set_defaults(func=classify_command)

    p_panel = sub.add_parser("panel", help="Aggregate classified documents into city-month panel CSVs.")
    p_panel.add_argument("--classified", default="outputs/nev_policy_panel/nev_classified.jsonl")
    p_panel.add_argument("--output-dir", default="outputs/nev_policy_panel")
    p_panel.add_argument("--min-confidence", type=float, default=0.55)
    p_panel.add_argument("--start-month", default="")
    p_panel.add_argument("--end-month", default="")
    p_panel.add_argument("--documents-csv", default="nev_policy_documents.csv")
    p_panel.add_argument("--expanded-csv", default="nev_policy_expanded_city_month.csv")
    p_panel.add_argument("--panel-csv", default="nev_policy_city_month_panel.csv")
    p_panel.add_argument("--central-panel-csv", default="nev_policy_central_month_panel.csv")
    p_panel.add_argument("--province-panel-csv", default="nev_policy_province_month_panel.csv")
    p_panel.add_argument("--prefecture-panel-csv", default="nev_policy_prefecture_month_panel.csv")
    p_panel.add_argument("--summary-json", default="nev_policy_summary.json")
    p_panel.set_defaults(func=panel_command)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
