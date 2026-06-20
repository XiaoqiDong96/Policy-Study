# 新能源汽车产业政策 LLM 判断逻辑说明

生成时间：2026-06-04

最近更新：2026-06-09。根据 8B/9B 三模型测试，新增“导向型产业政策”分类、答复/预算/报告类回顾文本排除规则、智能网联汽车边界规则，以及候选上下文后处理校准规则。

对应脚本：

- `scripts/nev_policy_pipeline.py`
- `scripts/materialize_nev_audit_outputs.py`

本文档说明当前项目中本地大模型如何判断一条候选法规是否属于“新能源汽车产业政策”，以及如何进一步分类政策工具、政策方向、事前/事后、供给/需求、强度、覆盖广度和复核风险。

注意：LLM 判断发生在 BeautifulSoup/关键词规则初筛之后。初筛只负责找“新能源汽车相关候选”，LLM 负责判断候选是否真正属于“新能源汽车产业政策”。

## 1. LLM 判断对象

进入 LLM 的对象不是全量法规原文，而是已经过初筛的候选文件。

每个候选文件包含：

| 输入字段 | 含义 |
|---|---|
| `id` | 法规文件 ID |
| `title` | 文件标题 |
| `province` | 数据库中的省份字段 |
| `pub_depart` | 发布部门 |
| `law_type` | 法规类型 |
| `pub_date` | 公布日期 |
| `use_date` | 施行日期 |
| `category_1/category_2` | 原始分类字段 |
| `candidate_score` | BeautifulSoup/关键词候选分数 |
| `nev_keyword_hits` | 新能源汽车相关命中词 |
| `policy_keyword_hits` | 政策语境命中词 |
| `llm_body` | 提供给模型的正文片段 |

其中 `llm_body` 不是简单截断全文，而是由以下内容拼接：

1. 正文开头片段；
2. 新能源汽车关键词附近窗口；
3. 正文末尾片段；
4. 合并重叠窗口；
5. 控制在 `--max-body-chars` 范围内。

当前测试设置：

```bash
--max-body-chars 3500
--num-ctx 8192
--llm-timeout 240
```

这样做的目的，是让模型看到政策主旨、关键词上下文、执行条款和结尾责任条款，同时控制本地模型推理成本。

## 2. 当前使用的模型组合

当前完整 LLM 测试支持两档三模型对抗判断。4B 档速度更快，8B/9B 档用于提示词和边界样本验证。

| 模型名 | 公司/团队 | 用途 |
|---|---|---|
| `gemma3:4b-mirror` | Google | 独立判断 |
| `qwen3:4b-mirror` | Alibaba/Qwen | 独立判断 |
| `llama3.2:3b-mirror` | Meta | 独立判断 |
| `gemma2:9b-mirror` | Google | 8B/9B 档独立判断 |
| `qwen3:8b-mirror` | Alibaba/Qwen | 8B/9B 档独立判断 |
| `llama3.1:8b-mirror` | Meta | 8B/9B 档独立判断 |

三个模型分别读取同一份 prompt 和同一条候选文件，各自输出结构化 JSON。最终结果不是取某一个模型，而是做严格多数投票和字段聚合。

Ollama 调用设置：

```json
{
  "stream": false,
  "format": "json",
  "options": {
    "temperature": 0,
    "num_ctx": 8192
  }
}
```

`temperature=0` 是为了降低随机性，提升可复现性。`format=json` 要求模型直接输出 JSON。Python 调用时显式关闭代理，直连：

```text
http://127.0.0.1:11434/api/generate
```

## 3. 系统提示词的核心口径

系统提示词要求模型扮演“严谨的中国产业政策研究助理”，并采用 Fang, Li, and Lu (2025) *Decoding China's Industrial Policies* 的窄口径产业政策定义。

核心定义可概括为：

> 政府为了改变长期经济结构，对特定产业或特定经济活动采取选择性、定向性干预；政府影响不同行业的相对价格，或用其能够影响/控制的资源配置手段，引导资源流向特定产业或活动。

按照该文献，产业政策判断包含四个必要条件：

| 条件 | 说明 |
|---|---|
| 政府主体发布 | 文件应来自政府、人大、部门、地方政府、开发区管委会等公共权力主体 |
| 有具体政策措施或明确导向 | 具体工具可以通过；正式规划/纲要/指导意见中把特定产业列为优先主题、重点任务、重大工程、发展目标或资源配置方向，也可以作为导向型产业政策 |
| 直接偏向特定产业或经济活动 | 一般政策即使间接受益某些产业，也不算产业政策 |
| 影响长期经济结构或资源配置 | 短期冲击应对、临时事务、周期性纾困通常不算产业政策 |

新增的关键约束是“直接目标”原则：模型必须区分直接目标产业、文本中顺带提及的产业、以及可能获得溢出收益的产业。对于新能源汽车研究，只有当文件直接针对新能源汽车整车、纯电动/插电混动/燃料电池汽车、动力电池、车用氢能、充换电/换电基础设施，或与新能源汽车直接绑定的智能网联汽车时，才可判为新能源汽车相关。

监管性政策不再被排除。按照该文献，产业政策 tone 可以是 supportive、regulatory 或 suppressive；在本项目字段中，regulatory/suppressive 统一进入 `restrict` 或 `mixed`。因此，直接针对新能源汽车产业的准入、标准、质量安全、环保监管、市场监管、目录资格等，也可以构成产业政策。

系统提示词同时列出通常不应判为产业政策的类型：

| 通常排除类型 | 原因 |
|---|---|
| 会议通知 | 多数只安排会议，不构成产业政策措施 |
| 工作总结 | 回顾性材料，通常没有新的政策工具 |
| 一般口号 | 没有可执行政策措施 |
| 部门搬迁 | 行政事务，不是产业政策 |
| 人事任免 | 组织人事信息，不是产业政策 |
| 短期疫情纾困 | 若不直接偏向新能源汽车产业，通常不是本研究口径 |
| 泛化宏观政策 | 面向所有行业，不直接偏向新能源汽车产业 |
| 普通交通管理 | 交通秩序、安全、通行管理若不含产业支持/限制工具，通常不是产业政策 |
| 单纯企业名单 | 只有名单而无政策措施时通常排除 |

2026-06-09 后新增一条更硬的边界：必须区分“本文件出台的政策”和“本文件引用/回顾的既有政策”。人大政协建议/提案答复、预算执行决议、统计公报、年度报告、工作报告、总结材料中，即使出现“已免征新能源汽车购置税/车船税”“新能源汽车产销量增长”“此前支持充电桩建设”等表述，通常只是答复、汇报或回顾，不代表该文件本身出台新能源汽车产业政策，应判为 false。

同时，`智能网联汽车` 只有在与新能源汽车、动力电池、车用氢能、充换电等核心新能源汽车目标直接绑定时才纳入。单独的智慧城市基础设施与智能网联汽车协同试点，不自动算新能源汽车产业政策。普通消费型锂电池、普通车辆运输、安全管理等也不纳入，除非正文明确指向动力电池或新能源汽车直接产业链。

## 3A. 导向型产业政策

用户确认后，当前口径不再要求所有产业政策都具有补贴标准、申报细则或可操作项目。有些产业政策只有导向作用，但仍可能满足产业政策定义。

导向型产业政策需要同时满足：

| 条件 | 说明 |
|---|---|
| 政府或政府部门发布 | 主体仍需符合公共权力主体要求 |
| 直接指向新能源汽车或直接产业链 | 不能只是“新能源”“绿色低碳”“汽车”“智能网联”等泛化词 |
| 有正式导向安排 | 例如优先主题、重点方向、重点任务、重大工程、发展目标、技术路线、产业布局、资源配置方向 |
| 影响长期结构或资源配置 | 规划、纲要、指导意见、行动计划等通常属于事前导向 |

这类样本使用：

```text
measure_specificity = guidance_only
```

如果既有导向又有可执行工具，则使用：

```text
measure_specificity = mixed
```

## 4. 新能源汽车范围

Prompt 中对新能源汽车范围做了明确限定：

| 范围 | 说明 |
|---|---|
| 新能源汽车整车 | 纯电动、插电混动、燃料电池汽车等 |
| 纯电动/插电混动/燃料电池汽车 | 车型和技术路线 |
| 动力电池 | 车用动力电池、回收利用、生产、研发等 |
| 车用氢能 | 氢燃料电池汽车、车用氢能基础设施等 |
| 充换电/换电基础设施 | 充电桩、充电站、换电站、充换电网络 |
| 智能网联汽车相关部分 | 只有与新能源汽车产业直接相关时纳入 |

需要注意：模型不能因为文本里偶然出现“汽车”“新能源”“充电”等词就判为政策。必须结合标题、主体、内容和措施判断。

## 5. 对抗式判断逻辑

模型被要求先做反方判断，再做最终判断。

具体要求：

1. 先认真寻找“它不是新能源汽车产业政策”的最强理由；
2. 再综合标题、元数据、正文片段判断；
3. 输出字段 `adversarial_not_policy_case`；
4. 输出字段 `decision_reason`；
5. 不能只因关键词命中就判定为产业政策。

这样设计是为了降低关键词误召回造成的假阳性。例如：

| 可能误判场景 | 反方理由示例 |
|---|---|
| 电动自行车管理 | 虽有“电动”，但不是新能源汽车产业 |
| 充电安全检查 | 若只是安全检查，可能不是产业政策 |
| 招标公告 | 若只是采购流程公告，不一定是产业政策 |
| 会议培训通知 | 没有具体产业工具 |
| 企业名单公示 | 如果没有配套政策措施，通常不是产业政策 |
| 普通交通限行 | 若只是交通管理，不直接影响新能源汽车产业结构 |

`adversarial_not_policy_case` 不是最终结论，而是强制模型记录反方理由，方便人工复核。

## 6. 单模型必须输出的 JSON 字段

每个模型必须输出一个 JSON 对象。

字段如下：

| 字段 | 类型 | 含义 |
|---|---|---|
| `is_nev_related` | bool | 是否与新能源汽车范围相关 |
| `is_industrial_policy` | bool | 是否属于窄口径产业政策 |
| `confidence_is_nev_related` | 0-1 | 新能源汽车相关判断置信度 |
| `confidence_is_industrial_policy` | 0-1 | 产业政策判断置信度 |
| `classification_confidence` | 0-1 | 综合分类置信度 |
| `false_positive_risk` | string | 误判风险，通常 low/medium/high |
| `adversarial_not_policy_case` | string | 最强反方理由 |
| `decision_reason` | string | 最终判断理由 |
| `policy_tone` | enum | 支持/限制/混合/中性/不确定 |
| `timing` | enum | 事前/事后/混合/不确定 |
| `policy_side` | enum | 供给/需求/两者/生态系统/不确定 |
| `measure_specificity` | enum | 导向型/具体措施/混合/不确定 |
| `direct_target_evidence` | string | 证明新能源汽车为直接目标的原文短语 |
| `measure_or_guidance_evidence` | string | 证明具体措施或导向安排的原文短语 |
| `policy_tools` | list | 政策工具 ID 列表 |
| `target_segments` | list | 政策目标环节 |
| `specific_measures` | list | 关键措施 |
| `eligibility_conditions` | list | 资格、门槛、申报条件等 |
| `implementation_mechanisms` | list | 考核、监督、部门协同等机制 |
| `strength_score` | 0-5 | 政策强度 |
| `coverage_breadth_score` | 0-5 | 覆盖广度 |

如果模型输出不是合法 JSON，脚本会尝试抽取首尾 `{...}` 中的 JSON。如果仍失败，则该模型记为调用失败，不参与投票。

## 7. 产业政策通过条件

单个模型判断一条文件为新能源汽车产业政策，需要同时满足：

```text
is_nev_related == true
is_industrial_policy == true
```

也就是说，以下情况都不能算作最终政策通过：

| 情况 | 是否通过 |
|---|---|
| 是新能源汽车相关，但不是产业政策 | 不通过 |
| 是产业政策，但不是新能源汽车相关 | 不通过 |
| 两者都不确定或任一为 false | 不通过 |

这能防止两类错误：

1. 把新能源汽车新闻、通知、公告、名单误判为产业政策；
2. 把一般产业政策或宏观政策误判为新能源汽车政策。

## 8. 政策工具字典

模型只能从以下 20 个工具 ID 中选择 `policy_tools`。

| 工具 ID | 英文标签 | 工具组 |
|---|---|---|
| `credit_finance` | Credit and Finance | `fiscal_financial` |
| `tax_incentives` | Tax Incentives | `fiscal_financial` |
| `equity_support` | Equity Support | `fiscal_financial` |
| `fiscal_subsidies` | Fiscal Subsidies | `fiscal_financial` |
| `industrial_fund` | Industrial Fund | `entry_regulation` |
| `promote_entrepreneurship` | Promote Entrepreneurship | `entry_regulation` |
| `investment_policy` | Investment Policy | `entry_regulation` |
| `business_environment` | Improving Business Environment | `entry_regulation` |
| `market_access_regulation` | Market Access and Regulation | `entry_regulation` |
| `trade_protection` | Trade Protection | `entry_regulation` |
| `labor_policy` | Labor Policy | `input_policy` |
| `preferential_land_supply` | Preferential Land Supply | `input_policy` |
| `infrastructure_investment` | Infrastructure Investment | `input_policy` |
| `technology_rd_adoption` | Technology R&D and Adoption | `input_policy` |
| `environmental_policy` | Environmental Policy | `input_policy` |
| `consumer_subsidy` | Consumer Subsidy | `demand_side` |
| `government_procurement` | Government Procurement | `demand_side` |
| `industrial_promotion` | Industrial Promotion | `demand_side` |
| `industrial_cluster` | Promote Industrial Cluster | `supply_chain` |
| `localization_policy` | Localization Policy | `supply_chain` |

工具组共有五类：

| 工具组 | 含义 |
|---|---|
| `fiscal_financial` | 财政、税收、金融、股权等支持 |
| `entry_regulation` | 进入、准入、投资、营商环境、创业、贸易保护等 |
| `input_policy` | 土地、劳动力、基础设施、研发、环保等投入端政策 |
| `demand_side` | 消费补贴、政府采购、推广应用等需求端政策 |
| `supply_chain` | 产业集群、本地化、供应链政策 |

## 9. 支持/限制方向 `policy_tone`

可取值：

| 值 | 含义 |
|---|---|
| `support` | 扶持、促进、补贴、奖励、便利化、鼓励建设等 |
| `restrict` | 限制、压减、处罚、淘汰、强监管、准入收紧等 |
| `mixed` | 同时包含明显扶持和限制 |
| `neutral` | 主要为中性安排、程序或管理 |
| `uncertain` | 无法明确判断 |

例子：

| 文本特征 | 倾向 |
|---|---|
| “给予购车补贴”“支持充电设施建设” | `support` |
| “不符合标准不得进入目录”“限期整改”“处罚” | `restrict` |
| “给予补贴，同时强化准入和处罚” | `mixed` |

## 10. 事前/事后 `timing`

可取值：

| 值 | 含义 |
|---|---|
| `ex_ante` | 事前安排 |
| `ex_post` | 事后评价、清算、检查、处罚 |
| `mixed` | 事前和事后都有 |
| `uncertain` | 无法明确判断 |

事前例子：

- 准入；
- 规划；
- 标准；
- 预算；
- 项目申报；
- 建设计划；
- 预防性监管。

事后例子：

- 绩效评价；
- 补贴清算；
- 追责；
- 检查；
- 处罚；
- 复核；
- 验收。

## 11. 供给/需求 `policy_side`

可取值：

| 值 | 含义 |
|---|---|
| `supply` | 面向企业生产、研发、投资、土地、融资、供应链 |
| `demand` | 面向消费者购买、公交出租采购、政府采购、市场推广 |
| `both` | 供给端和需求端都明显存在 |
| `ecosystem` | 充换电基础设施、标准平台、公共服务等供需难分 |
| `uncertain` | 无法明确判断 |

例子：

| 政策内容 | 可能分类 |
|---|---|
| 支持整车企业研发、动力电池项目投资 | `supply` |
| 消费者购车补贴、公交出租新能源替换 | `demand` |
| 同时补贴企业研发和消费者购车 | `both` |
| 建设充电桩、换电站、公共平台、标准体系 | `ecosystem` |

## 12. 强度 `strength_score`

取值 0-5。

| 分值 | 含义 |
|---:|---|
| 0 | 不是政策 |
| 1 | 泛泛表述 |
| 2 | 有方向但措施弱 |
| 3 | 有具体措施或责任 |
| 4 | 有资金、资格、指标、期限、项目或监管机制 |
| 5 | 有明确预算、补贴标准、强制指标、处罚、考核等高约束安排 |

强度不是“政策好坏”，而是政策约束和可执行程度。

## 13. 覆盖广度 `coverage_breadth_score`

取值 0-5。

综合考虑：

| 维度 | 说明 |
|---|---|
| 目标环节数量 | 是否涉及整车、电池、充电、回收、研发、销售等多个环节 |
| 工具数量 | 是否同时使用补贴、准入、采购、基础设施、研发等多个工具 |
| 执行主体数量 | 是否涉及多部门、多层级、多主体 |
| 空间覆盖 | 国家、省、市、园区等覆盖范围 |
| 产业链覆盖 | 是否覆盖产业链上下游 |

覆盖广度越高，说明政策影响面越广，不代表强度一定越高。

## 14. 单模型输出规范化

模型返回后，脚本会规范化字段：

1. `policy_tools` 只能保留工具字典中的合法 ID；
2. `policy_tone` 不在合法枚举中则改为 `uncertain`；
3. `timing` 不在合法枚举中则改为 `uncertain`；
4. `policy_side` 不在合法枚举中则改为 `uncertain`；
5. `strength_score`、`coverage_breadth_score` 被限制在 0-5；
6. 如果不是同时满足 `is_nev_related` 和 `is_industrial_policy`，则清空政策工具、措施、对象和机制，并把强度、广度设为 0。

第 6 点非常重要：即使模型误填了政策工具，只要最终不是新能源汽车产业政策，这些工具不会进入结果表和面板。

## 15. 三模型投票规则

每个模型独立判断后，脚本做集成。

成功模型数记为 `n`。如果某模型调用失败或 JSON 解析失败，该模型不参与成功投票。

核心票数：

| 字段 | 含义 |
|---|---|
| `nev_yes_votes` | 认为新能源汽车相关的模型数 |
| `industrial_yes_votes` | 认为产业政策的模型数 |
| `policy_yes_votes` | 同时认为新能源汽车相关且是产业政策的模型数 |
| `policy_vote_share` | `policy_yes_votes / models_succeeded` |

最终通过规则：

```text
policy_yes_votes > models_succeeded / 2
```

也就是严格多数。

在三个模型都成功时：

| 投票 | 最终结果 |
|---|---|
| 3/3 认为是政策 | 通过 |
| 2/3 认为是政策 | 通过 |
| 1/3 认为是政策 | 不通过 |
| 0/3 认为是政策 | 不通过 |

如果只有两个模型成功：

| 投票 | 最终结果 |
|---|---|
| 2/2 认为是政策 | 通过 |
| 1/2 认为是政策 | 不通过，因为没有严格多数 |
| 0/2 认为是政策 | 不通过 |

如果只有一个模型成功：

| 投票 | 最终结果 |
|---|---|
| 1/1 认为是政策 | 通过，但会因为模型失败数大于 0 被纳入复核 |
| 0/1 认为是政策 | 不通过 |

如果所有模型都失败：

- 最终判为不是政策；
- 置信度为 0；
- 标记为待重跑或人工复核。

## 16. 字段聚合规则

最终字段不是简单取第一个模型，而是按以下规则聚合。

| 字段 | 聚合方法 |
|---|---|
| `is_nev_related` | 新能源相关票数严格多数 |
| `is_industrial_policy` | `policy_yes_votes` 严格多数 |
| `policy_tone` | 在参与聚合的模型中取多数值 |
| `timing` | 多数值 |
| `policy_side` | 多数值 |
| `policy_tools` | 在“判为政策”的模型中达到工具多数阈值的工具 |
| `target_segments` | 取并集 |
| `specific_measures` | 取并集，最多保留 12 条 |
| `eligibility_conditions` | 取并集，最多保留 12 条 |
| `implementation_mechanisms` | 取并集，最多保留 12 条 |
| `strength_score` | 参与聚合模型分值平均后四舍五入 |
| `coverage_breadth_score` | 参与聚合模型分值平均后四舍五入 |

工具聚合阈值：

```text
tool_threshold = floor(判为政策的模型数 / 2) + 1
```

例如：

| 判为政策的模型数 | 工具进入最终结果所需票数 |
|---:|---:|
| 1 | 1 |
| 2 | 2 |
| 3 | 2 |

如果最终判为政策，但工具聚合为空，脚本会退回使用第一个判为政策模型的工具列表，避免政策通过但工具完全为空。不过这种情况会被后续复核规则捕捉。

## 17. 置信度计算

脚本会保留模型给出的三个置信度：

| 字段 | 含义 |
|---|---|
| `confidence_is_nev_related` | 新能源相关置信度 |
| `confidence_is_industrial_policy` | 产业政策置信度 |
| `classification_confidence` | 综合分类置信度 |

聚合时先计算平均置信度，再乘以投票一致性因子。

一致性因子：

```text
如果最终通过：agreement_multiplier = policy_vote_share
如果最终不通过：agreement_multiplier = 1 - policy_vote_share
如果模型之间有分歧：agreement_multiplier 再乘 0.85
```

最终综合置信度：

```text
final_confidence = avg_classification_confidence * agreement_multiplier
```

因此：

- 全票一致且模型自信，最终置信度较高；
- 2/3 勉强通过，置信度会低于 3/3；
- 模型有分歧时，置信度会进一步折减；
- 1/2 平票不通过，且会因为非全票一致进入复核。

## 18. 模型分歧指标

结果中保留以下集成字段：

| 字段 | 含义 |
|---|---|
| `models_requested` | 请求的模型列表 |
| `models_succeeded` | 成功返回并解析的模型数 |
| `models_failed` | 调用失败或解析失败的模型数 |
| `companies_succeeded` | 成功参与判断的模型公司 |
| `policy_yes_votes` | 认为是新能源汽车产业政策的票数 |
| `policy_vote_share` | 政策赞成票比例 |
| `classification_disagreement` | 是否存在模型之间政策判断不一致 |
| `tool_jaccard_mean` | 判为政策模型之间工具集合的平均 Jaccard 相似度 |

`tool_jaccard_mean` 用于观察模型对政策工具的分歧程度。越接近 1，工具选择越一致；越接近 0，工具选择差异越大。

## 19. 问题样本和复核文件夹规则

完成 LLM 分类后，脚本会把需要人工复核的样本复制到：

```text
review_problem_cases/
```

进入复核文件夹的条件包括：

| 条件 | 说明 |
|---|---|
| 模型投票不一致 | `classification_disagreement == true` |
| 存在模型调用失败 | `models_failed > 0` |
| 政策判断非全票一致 | `policy_vote_share` 不是 0 或 1 |
| 综合置信度低 | 三个置信度的最小值低于 0.65 |
| 误判风险较高 | `false_positive_risk` 为 medium/high/中/高 |
| 判为产业政策但工具为空 | 说明工具编码可能异常 |
| LLM 错误字段非空 | 任一模型调用或解析错误被记录 |

这些样本并不一定错误，只是更值得人工复核。

## 20. TXT 文件逻辑

每个候选都会生成一个独立 `.txt` 文件，放在：

```text
txt_all/
```

TXT 文件包含：

1. 序号；
2. id；
3. 标题；
4. 省份；
5. 发文部门；
6. 法规类型；
7. 文号；
8. 公布日期；
9. 施行日期；
10. 月份字段；
11. 原始链接字段；
12. 候选分数；
13. 新能源汽车命中词；
14. 政策语境命中词；
15. LLM 分类结果；
16. 三模型投票结果；
17. 对抗理由；
18. 最终判断理由；
19. LLM 输入正文片段。

在 LLM 完成前，TXT 先只包含候选信息和 LLM 输入正文片段。LLM 完成后，脚本会重新生成带分类结果的 TXT。

复核文件夹 `review_problem_cases/` 中的 TXT 是从 `txt_all/` 复制过去的，内容相同，只是表示该样本触发了复核条件。

## 21. Excel 结果表逻辑

LLM 全部完成后，脚本生成：

```text
nev_llm_results.xlsx
```

Excel 包含四个工作表：

| Sheet | 内容 |
|---|---|
| `Summary` | 总体摘要：候选数、产业政策数、复核数、tone 和 tool 统计 |
| `Results` | 每条候选的最终聚合结果 |
| `Problems` | 触发复核规则的样本 |
| `ModelVotes` | 每个模型对每条样本的单独判断 |

`Results` 表核心字段：

| 字段 | 含义 |
|---|---|
| `seq` | 1000 条候选中的序号 |
| `id` | 法规 ID |
| `title` | 标题 |
| `province` | 省份 |
| `pub_depart` | 发文部门 |
| `law_type` | 法规类型 |
| `candidate_score` | 初筛候选分数 |
| `nev_keyword_hits` | 新能源汽车命中词 |
| `policy_keyword_hits` | 政策语境命中词 |
| `is_nev_related` | 聚合后是否新能源汽车相关 |
| `is_industrial_policy` | 聚合后是否新能源汽车产业政策 |
| `classification_confidence` | 聚合综合置信度 |
| `policy_tone` | 支持/限制等方向 |
| `timing` | 事前/事后 |
| `policy_side` | 供给/需求 |
| `policy_tools` | 政策工具 |
| `strength_score` | 强度 |
| `coverage_breadth_score` | 覆盖广度 |
| `policy_yes_votes` | 支持票数 |
| `policy_vote_share` | 支持票占比 |
| `problem_flag` | 是否进入复核 |
| `problem_reasons` | 复核原因 |
| `txt_path` | 对应 TXT 路径 |
| `review_txt_path` | 如果进入复核，对应复核 TXT 路径 |

## 22. 面板生成关系

只有满足以下条件的文件才会进入后续政策明细和地级市-月份面板：

```text
is_nev_related == true
is_industrial_policy == true
classification_confidence >= min_confidence
```

当前测试面板使用：

```bash
--min-confidence 0.55
```

进入面板后，文件会按行政范围扩展：

- 国家层面政策影响所有地级市；
- 省级政策影响该省所有地级市；
- 市级政策影响对应地级市；
- 直辖市按地级市单元处理。

面板统计的指标包括：

- 政策数量；
- 置信度加权数量；
- 强度总和/均值；
- 覆盖广度总和/均值；
- 支持/限制方向数量；
- 事前/事后数量；
- 供给/需求数量；
- 工具组数量；
- 单个政策工具数量。

## 23. 当前 LLM 工作流顺序

当前 `tailfilled1000_nev_candidates_llm_audit` 流程如下：

1. 从全样本尾部窗口开始扫描；
2. BeautifulSoup/关键词初筛新能源汽车相关候选；
3. 候选达到 1000 条后停止；
4. 保存 `nev_candidates_tailfilled1000.jsonl`；
5. 为 1000 条候选逐条生成 TXT；
6. 使用三个模型逐条分类；
7. 保存 `nev_classified_tailfilled1000.jsonl`；
8. 重新生成带分类结果的 TXT；
9. 将问题样本复制到 `review_problem_cases/`；
10. 生成 `nev_llm_results.xlsx`；
11. 生成地级市-月份测试面板。

## 24. 结果解释注意事项

1. LLM 分类结果不是人工最终定稿，尤其是进入 `review_problem_cases/` 的样本需要人工抽查。
2. `is_industrial_policy=false` 不代表文件不重要，只代表不属于本研究窄口径新能源汽车产业政策。
3. `policy_tools` 是研究编码工具，不是法规原文中的官方分类。
4. `strength_score` 和 `coverage_breadth_score` 是编码指标，用于后续计量构造，不应解释为政策优劣。
5. 2/3 通过的样本会进入结果，但置信度低于 3/3，并会因“政策判断非全票一致”进入复核。
6. 全票不通过但置信度低的样本也会进入复核，避免错杀边界政策。
7. 对抗理由字段应当和最终判断一起看：反方理由强但最终通过的样本，通常是最值得复核的样本。

## 25. 与文献思路的对应

当前 LLM 判断继承了用户提供文献的几个核心思路：

1. 使用窄口径产业政策定义，而不是把所有政府文件都算作政策；
2. 强调具体政策工具，而不是只看口号或规划文字；
3. 区分政策工具类型；
4. 区分政策作用方向和作用环节；
5. 允许一个文件包含多个工具；
6. 通过对抗式判断降低关键词误判；
7. 通过多模型投票降低单模型偏差；
8. 通过复核文件夹保留边界样本，便于人工校准。

## 26. 推荐人工复核顺序

建议人工优先看以下样本：

1. `review_problem_cases/` 中 `problem_reasons` 包含“模型投票不一致”的样本；
2. `policy_vote_share=0.666...` 的 2/3 通过样本；
3. `classification_confidence < 0.65` 的样本；
4. 判为政策但 `policy_tools` 少或不直观的样本；
5. `adversarial_not_policy_case` 很强，但最终仍通过的样本；
6. 涉及“智能网联汽车”“动力电池”“充电设施”但正文语义不清的样本。

这些样本复核后，可以反过来调整 prompt、候选关键词、复核阈值或工具口径。
