# Theme Chokepoint Scoring Contract v1

## 1. 合同状态

| 字段 | 值 |
| --- | --- |
| 合同版本 | `theme-chokepoint-scoring-v1` |
| 状态 | Frozen for v1 implementation and Golden Case evaluation |
| 冻结日期 | 2026-08-15 |
| 适用产品 | Theme Chokepoint Research Agent |
| 语言 | 中文说明，英文枚举和字段名作为实现合同 |

“冻结”表示：v1 的实现、测试、研究快照和报告必须遵循本合同。冻结不表示权重已经获得市场数据、回测或跨行业验证。后续改变权重、评分锚点、门槛、状态语义或公式时，必须创建新版本，不得改写使用旧版本产生的历史运行。

## 2. 合同目标

本合同将四个不同问题分开评分：

1. `segment_chokepoint`：供应链环节是否构成真实约束；
2. `company_defensibility`：当前控制者在指定范围和期限内有多难被替代；
3. `replacement_momentum`：挑战者或替代路线距离真实替代还有多远；
4. `earnings_transmission`：卡点、扩产或替代能否传导到公司收入和利润。

四套评分不得合成一个默认“投资总分”。估值、股价预期和买卖建议不属于本合同。

## 3. 评分对象与范围键

任何公司评分必须绑定以下范围：

```text
company_id
segment_id
product_id
region
time_horizon_months
as_of_date
```

任何 Segment 评分必须绑定：

```text
segment_id
product_id
theme_id
region
time_horizon_months
as_of_date
```

不得脱离范围输出“公司不可替代”“公司将被替代”或“该环节永久是卡点”。

## 4. 通用证据合同

### 4.1 Claim 状态

每个评分维度必须引用稳定的 `claim_id`。Claim 的证据状态只能是：

| 状态 | 含义 |
| --- | --- |
| `supported` | 当前证据足以支持一个明确评分区间 |
| `conflicted` | 可信来源之间存在未解决冲突，只能给出更宽评分区间 |
| `unknown` | 证据不足，不能确定评分 |

`model_confidence` 不能代替证据状态。

### 4.2 原文要求

用于评分的 Evidence Card 至少保存：

```text
evidence_id
claim_id
article_id/source_id
content_sha256或等价版本标识
exact_quote
page/section/text_offset（能够取得时）
publisher
source_type
publication_date
data_as_of
retrieved_at
stance: supports | contradicts | context_only
limitations
```

摘要、向量 chunk、搜索 snippet 和模型转述可以用于发现资料，但不能作为关键维度的唯一最终证据。

### 4.3 双向验证

候选卡点和公司不可替代性原则上必须寻找：

```text
至少一个供应侧来源
+ 至少一个客户或需求侧来源
+ 至少一次替代或反证搜索
```

例外必须在结果中记录 `evidence_exception_reason`，并且不能获得 `high` 证据等级。

### 4.4 证据质量

证据质量单独输出，不加入业务分数：

| 等级 | 最低语义 |
| --- | --- |
| `high` | 关键 Claim 有直接原文、一手或监管来源、跨来源确认，并且没有未解决重大冲突 |
| `medium` | 有直接原文和至少一个高质量来源，但客户侧、独立确认或部分关键字段仍缺失 |
| `low` | 主要依赖公司自述、二手报道、评论或间接推断 |

### 4.5 时效性

| Claim 类型 | v1 默认时效规则 |
| --- | --- |
| 当前价格、交期、利用率、订单、认证进度 | 原则上不超过180天 |
| 当前有效产能、客户采购状态、竞争份额 | 原则上不超过12个月，并检查是否有更新 |
| 工艺、认证结构、技术依赖、监管要求 | 原则上不超过24个月，除非存在仍有效的官方规则或未被取代的技术文件 |
| 已发生的历史里程碑 | 不因时间自动失效，但不能单独证明当前状态 |

超过默认时效的证据可以保留为历史事实，但当前状态应标记 `stale` 或产生刷新问题。

## 5. 通用评分方法

### 5.1 维度等级

每个维度使用0到4的有序等级：

| 等级 | 通用含义 |
| ---: | --- |
| 0 | 证据明确反对该维度的正向判断 |
| 1 | 弱，只有轻微或早期正向迹象 |
| 2 | 中等，已有实质证据但限制、替代或执行不确定性明显 |
| 3 | 强，多项直接证据支持且主要反证不足以推翻 |
| 4 | 很强，直接、交叉、当前证据支持，且反证搜索未发现可在期限内改变结论的路径 |

各维度表给出0、2、4的锚点。1和3分别表示相邻锚点之间，并且必须说明为什么没有落在更低或更高等级。

### 5.2 评分区间

每个维度保存：

```text
rating_min: 0..4
rating_max: 0..4
evidence_state: supported | conflicted | unknown
```

规则：

- `supported`：`rating_min == rating_max`；
- `conflicted`：使用可信证据能够支持的最低和最高等级；
- `unknown`：`rating_min = 0`，`rating_max = 4`。

维度分数：

```text
dimension_points_min = weight × rating_min / 4
dimension_points_max = weight × rating_max / 4
```

总分：

```text
score_min = sum(dimension_points_min)
score_max = sum(dimension_points_max)
```

所有分数保存到小数点后一位，展示时可以四舍五入为整数，但原始值必须保留。

### 5.3 Evidence Coverage

```text
evidence_coverage
= 已经不是unknown的维度权重之和 / 100
```

`conflicted` 表示已有证据，因此计入 Coverage，但会扩大评分区间并降低证据质量。

### 5.4 Score Width

```text
score_width = score_max - score_min
```

v1 默认解释：

| Score Width | 解释 |
| ---: | --- |
| `<= 10` | 不确定性较窄 |
| `> 10 and <= 25` | 中等不确定性 |
| `> 25` | 结论高度依赖未决证据 |

评分排名不得隐藏 `score_width`。

## 6. Segment Chokepoint Score

### 6.1 权重

| 维度 | 权重 |
| --- | ---: |
| `demand_pressure` | 15 |
| `downstream_criticality` | 20 |
| `effective_supply_concentration` | 15 |
| `qualification_barrier` | 15 |
| `capacity_inelasticity` | 15 |
| `substitute_weakness` | 10 |
| `constraint_persistence` | 10 |
| **合计** | **100** |

### 6.2 评分锚点

| 维度 | 0分锚点 | 2分锚点 | 4分锚点 |
| --- | --- | --- | --- |
| Demand Pressure | 没有增量需求，或需求下降且未传导到该环节 | 有需求增长迹象，但单位用量、渗透率、库存或时间范围仍有明显不确定性 | 客户侧、数量或规格证据清楚表明增量需求在目标期限内传导到该环节 |
| Downstream Criticality | 可轻易绕过、替换或缺少该环节也不影响交付 | 缺少供应会降低规格、提高成本或造成可管理延迟 | 缺少供应会阻止出货、投产、合规或关键性能实现，且期限内没有可行绕行方案 |
| Effective Supply Concentration | 多个已认证供应商拥有可用产能 | 供应集中，但客户已有双供或存在部分可用替代产能 | 合格、稳定、可销售产出集中于极少数供应商或单一路线，且没有足够备用产能 |
| Qualification Barrier | 基本无需重新认证，或可在3个月内完成 | 通常需要6至12个月、客户测试或有限工艺迁移 | 通常需要18个月以上、监管/安全认证、重大重新设计或长期良率爬坡 |
| Capacity Inelasticity | 供应可在6个月内通过现有产能或标准扩产响应 | 扩产大致需要6至12个月，且仍受设备、认证或良率限制 | 合格可销售产出通常需要18个月以上，并受到建设、设备、认证、良率或原料多重约束 |
| Substitute Weakness | 已有合格、性能相当并具规模的替代路线 | 替代者已经验证或部分采用，但性能、产能、成本或兼容性仍有限 | 没有达到目标产品要求并具规模的合格替代者 |
| Constraint Persistence | 预计3个月内自然消退或由库存解决 | 可能持续6至12个月，但扩产或需求变化可能缓解 | 预计持续18个月以上，或扩产、认证和替代均无法在目标期限内解决 |

### 6.3 强制准入门槛

一个 Segment 只有同时满足以下条件，才能成为 `candidate_chokepoint`：

1. `demand_pressure.rating_min >= 2`；
2. `downstream_criticality.rating_min >= 2`；
3. `evidence_coverage >= 0.70`；
4. `score_min >= 65`；
5. 需求侧或客户侧直接证据存在；
6. 供应侧直接证据存在；
7. 替代和反证搜索已完成；
8. 两个强制维度不存在未解决 `conflicted` 状态。

状态规则：

| 状态 | 规则 |
| --- | --- |
| `strong_candidate_chokepoint` | 全部强制门槛通过，`score_min >= 80`，Coverage >= 0.85，证据质量为 `high` |
| `candidate_chokepoint` | 全部强制门槛通过，`score_min >= 65` |
| `watch_segment` | 强制维度未被反对，并且 `score_max >= 65`，但分数、Coverage或证据门槛尚未通过 |
| `not_supported` | `demand_pressure.rating_max < 2`、`downstream_criticality.rating_max < 2`，或 `score_max < 40` |
| `insufficient_evidence` | 关键输入为unknown，无法进入以上状态 |

旧的“六项满足四项”只保留为解释性检查，不再作为 v1 的正式准入规则。

## 7. Company Defensibility Score

### 7.1 权重

| 维度 | 权重 |
| --- | ---: |
| `technical_performance_gap` | 20 |
| `qualification_lock_in` | 20 |
| `switching_cost` | 15 |
| `qualified_effective_capacity` | 15 |
| `quality_delivery_reliability` | 10 |
| `ecosystem_installed_base` | 10 |
| `customer_sourcing_evidence` | 10 |
| **合计** | **100** |

### 7.2 评分锚点

| 维度 | 0分锚点 | 2分锚点 | 4分锚点 |
| --- | --- | --- | --- |
| Technical Performance Gap | 合格替代者已达到或超过目标要求 | 公司有可测量优势，但替代者能满足部分客户或产品 | 优势是目标产品必需条件，且合格替代者在关键性能、良率、可靠性或工艺上明显不足 |
| Qualification Lock-in | 客户可在3个月内切换且无需重大认证 | 切换通常需要6至12个月测试或认证 | 切换需要18个月以上、重大重新设计、监管/安全认证或长期量产验证 |
| Switching Cost | 切换成本和运营风险很低 | 需要中等迁移、设备、软件、库存或停机成本 | 切换会造成重大重新设计、停机、良率、保修、合规或供应风险 |
| Qualified Effective Capacity | 多家替代者拥有足够合格可用产能 | 公司拥有重要份额，但客户能够双供或逐步迁移 | 公司控制目标范围内大部分合格、稳定、可销售产出，替代者没有足够容量 |
| Quality and Delivery Reliability | 公司质量或交付落后，已有客户流失证据 | 表现稳定但并非显著优于替代者 | 客户和运营证据持续显示规模良率、缺陷率和交付可靠性显著领先 |
| Ecosystem and Installed Base | 产品可直接替换且没有生态锁定 | 有一定软件、接口、工具或存量集成成本 | 替代需要跨软件、标准、设备、流程和存量系统进行重大迁移 |
| Customer Sourcing Evidence | 客户已成功切换、广泛双供或主动降低依赖 | 客户仍依赖公司，但已有双供、验证或替代计划 | 客户侧证据显示单供、长期绑定或期限内没有合格替代者 |

### 7.3 Defensibility 状态

| 状态 | 规则 |
| --- | --- |
| `high_defensibility` | `score_min >= 70`、Coverage >= 0.70、客户侧证据存在，且 Technical/Qualification/Switching 中至少一个 `rating_min >= 3` |
| `low_defensibility` | `score_max < 50`、Coverage >= 0.70，且没有范围或时效缺口能够明显改变结论 |
| `medium_defensibility` | 不满足High或Low，但 Coverage >= 0.70 |
| `defensibility_uncertain` | Coverage < 0.70，或客户采购行为仍为unknown |

## 8. Replacement Momentum Score

### 8.1 权重

| 维度 | 权重 |
| --- | ---: |
| `performance_parity` | 15 |
| `cost_tco_advantage` | 10 |
| `qualification_progress` | 20 |
| `capacity_readiness` | 15 |
| `customer_adoption` | 15 |
| `ecosystem_compatibility` | 10 |
| `execution_funding` | 10 |
| `regulatory_architecture_tailwind` | 5 |
| **合计** | **100** |

### 8.2 评分锚点

| 维度 | 0分锚点 | 2分锚点 | 4分锚点 |
| --- | --- | --- | --- |
| Performance Parity | 未达到目标产品关键要求 | 已接近要求或只适用于部分客户/低端产品 | 已在目标产品中达到或超过关键性能、良率和可靠性要求 |
| Cost/TCO Advantage | 综合成本更高或没有可信数据 | 有局部成本优势，但迁移、良率或运营成本抵消部分收益 | 经客户或规模运营验证，综合成本显著更低 |
| Qualification Progress | 概念、原型或尚未送样 | 已送样并进入客户验证 | 已完成目标客户量产认证并保持有效 |
| Capacity Readiness | 只有计划或公告 | 资金、建设或设备已落实，但认证/良率/可销售产出仍有限 | 已具备合格、稳定、规模化可销售产出 |
| Customer Adoption | 没有客户验证或订单证据 | 已有客户验证、Design Win或小批量采用 | 已规模交付、形成实质收入并持续获得份额 |
| Ecosystem Compatibility | 需要重大生态迁移且没有解决方案 | 部分兼容，但仍需适配、工具或流程变化 | 可在目标生态中低摩擦采用，关键接口和工具已验证 |
| Execution and Funding | 资金、团队、供应链或执行记录不足 | 资源基本具备，但建设、良率或交付仍有明显风险 | 资金、团队、供应链和规模交付记录均得到验证 |
| Regulatory/Architecture Tailwind | 政策或架构明显不利 | 中性或影响仍不明确 | 政策、标准或架构变化明确降低替代壁垒并推动采用 |

### 8.3 替代成熟度

成熟度枚举：

```text
concept
prototype
customer_sample
customer_validation
design_win
qualification_complete
pilot_volume
mass_production
material_revenue
sustained_share_gain
```

成熟度必须由原文 Claim 支持，不得仅根据评分反推。

### 8.4 Replacement 状态

| 状态 | 规则 |
| --- | --- |
| `early_replacement_signal` | 已有正向证据，但未达到 credible/ready/realized门槛 |
| `credible_challenge` | `score_min >= 50`，成熟度至少为 `customer_validation` |
| `replacement_ready` | `score_min >= 65`、Coverage >= 0.70、成熟度至少为 `qualification_complete`，且 Capacity Readiness和Customer Adoption均 `rating_min >= 2` |
| `realized_replacement` | `score_min >= 75`、Coverage >= 0.80、成熟度至少为 `mass_production`，并且存在 `material_revenue` 或 `sustained_share_gain` 证据 |
| `replacement_uncertain` | 关键进度、产能或客户采用为unknown，不能进入以上状态 |

## 9. Earnings Transmission Score

### 9.1 权重

| 维度 | 权重 |
| --- | ---: |
| `segment_revenue_exposure` | 20 |
| `volume_capacity_leverage` | 15 |
| `pricing_power` | 15 |
| `margin_transmission` | 15 |
| `time_to_revenue` | 10 |
| `capital_intensity` | 10 |
| `customer_concentration_risk` | 5 |
| `persistence` | 10 |
| **合计** | **100** |

所有维度的高分方向均表示“更有利于在目标期限内形成可持续增量利润”。因此 `capital_intensity` 和 `customer_concentration_risk` 的高分分别表示资本负担可控、客户集中风险可控。

### 9.2 评分锚点

| 维度 | 0分锚点 | 2分锚点 | 4分锚点 |
| --- | --- | --- | --- |
| Segment Revenue Exposure | 当前和目标期限内均不重要，没有可信收入路径 | 可能占收入5%至15%，或从较低基数形成可见贡献 | 当前或目标期限内预计占收入30%以上，且业务口径和来源明确 |
| Volume/Capacity Leverage | 没有合格可销售产能承接需求 | 有部分可用产能或扩产，但认证、良率或时点仍限制兑现 | 有明确合格产能、订单和交付能力，可在期限内形成显著销量 |
| Pricing Power | 固定价格、客户压价或成本无法转嫁 | 有部分提价、产品结构或合同改善证据 | 多期数据和客户行为显示能够持续提价、改善mix或分配稀缺产能 |
| Margin Transmission | 增量收入被成本、良率、折旧或费用完全吸收 | 对毛利或利润有一定贡献，但成本和爬坡影响明显 | 增量收入以较高增量利润率转化为经营利润，并得到财务数据支持 |
| Time to Revenue | 预计收入确认超出研究期限 | 部分收入在期限内兑现，但主要贡献较晚或不确定 | 认证、产能、出货和确认时点均支持在目标期限内形成主要贡献 |
| Capital Intensity | 资本、折旧、营运资金和爬坡成本可能超过收益 | 投入较大但回报路径可见，现金和折旧压力仍需跟踪 | 可利用现有产能或高回报扩产，资本和爬坡负担相对可控 |
| Customer Concentration Risk | 单一或少数客户有能力压价、取消、内制或改变路线 | 客户集中但合同、需求或多客户扩展提供部分保护 | 客户结构分散，或合同/切换成本显著限制客户议价和取消风险 |
| Persistence | 主要来自一次性库存、短期涨价或订单提前 | 可能持续6至12个月，但需求或产能正常化后明显下降 | 多期需求、合同、结构性单位用量或长期产能约束支持18个月以上持续性 |

`segment_revenue_exposure` 的百分比是v1默认锚点。若业务从极低基数快速增长或公司披露口径无法直接获得，必须说明偏离锚点的理由并保留为新版本校准项。

### 9.3 Earnings 状态

| 状态 | 规则 |
| --- | --- |
| `material_earnings_path` | `score_min >= 65`、Coverage >= 0.70、Revenue Exposure和Time to Revenue均 `rating_min >= 2` |
| `weak_earnings_capture` | `score_max < 50`，或Revenue Exposure/Time to Revenue的 `rating_max < 2` |
| `earnings_path_uncertain` | 其余情况，或关键财务口径为unknown |

## 10. 公司竞争状态

公司竞争状态不得使用 Earnings 分数替代产业竞争判断；Earnings 状态作为单独标签展示。

| 公司状态 | 冻结规则 |
| --- | --- |
| `durable_chokepoint_owner` | Defensibility为High；所有已识别挑战者的Replacement `score_max < 50`；没有 `replacement_ready`；客户侧依赖证据当前有效 |
| `contested_chokepoint_owner` | Defensibility为High或Medium；至少一个挑战者达到 `credible_challenge` |
| `vulnerable_incumbent` | Defensibility为Low，或Defensibility `score_max < 60`；至少一个挑战者达到 `replacement_ready` |
| `commodity_or_weakly_differentiated` | Defensibility `score_max < 50`，且差异化、认证和切换证据均弱；是否存在当前挑战者不影响该标签 |
| `competition_state_uncertain` | Coverage、客户行为、挑战者范围或时间范围不足以进入以上状态 |

挑战者标签：

| 标签 | 规则 |
| --- | --- |
| `emerging_replacement` | Replacement达到 `credible_challenge` |
| `qualified_replacement` | Replacement达到 `replacement_ready` |
| `realized_replacement` | Replacement达到同名状态 |

## 11. 监控和状态迁移

新证据必须先更新具体 Claim、维度区间和成熟度，再重新计算状态。禁止直接根据一条新闻改写总状态。

### 11.1 Segment 迁移

| 变化 | 冻结规则 |
| --- | --- |
| `strengthening` | 新证据使 `score_min` 相对上一可比快照上升至少10分，或强制维度上升至少1级且准入状态增强 |
| `weakening` | 新证据使 `score_max` 下降至少10分，或强制维度下降至少1级且准入状态减弱 |
| `invalidated` | Demand Pressure或Downstream Criticality的 `rating_max < 2`，或状态成为 `not_supported` |
| `transferring` | 原节点至少Weakening；相邻节点至少Strengthening；底层Demand Pressure仍 `rating_min >= 2`；存在解释约束迁移的Dependency Edge和Claim |
| `unchanged` | 证据更新没有触发以上规则 |

### 11.2 公司迁移

公司状态变化必须由以下至少一种变化触发：

- Defensibility维度或状态变化；
- Replacement成熟度或状态变化；
- 新客户双供、切换、Design Win、认证、量产、实质收入或份额证据；
- 评估范围或时间窗口正式变更。

纯新闻数量、管理层重复表述或搜索热度不能单独触发公司竞争状态变化。

## 12. 标准输出合同

```json
{
  "scoring_version": "theme-chokepoint-scoring-v1",
  "assessment_scope": {
    "theme_id": "theme_001",
    "product_id": "product_001",
    "segment_id": "segment_001",
    "company_id": "company_001",
    "region": "global",
    "time_horizon_months": 12,
    "as_of_date": "2026-08-15"
  },
  "score_type": "company_defensibility",
  "dimensions": [
    {
      "name": "qualification_lock_in",
      "weight": 20,
      "rating_min": 3,
      "rating_max": 3,
      "evidence_state": "supported",
      "claim_ids": ["claim_101", "claim_102"],
      "rationale": "Customer-side evidence supports a long production qualification cycle."
    }
  ],
  "score_min": 67.5,
  "score_max": 77.5,
  "score_width": 10.0,
  "evidence_coverage": 0.8,
  "evidence_quality": "medium",
  "evidence_freshness": "current",
  "state": "medium_defensibility",
  "missing_material_questions": [
    "Has the largest customer completed dual-source qualification?"
  ]
}
```

## 13. 明确禁止的评分捷径

以下信息不能单独产生高分：

- 当前市场份额或公司规模；
- 专利数量；
- 管理层使用“领先”“护城河”“唯一”等措辞；
- 新闻或社交媒体提及次数；
- 宣布扩产金额；
- 发布样品、原型或路线图；
- 未绑定原文的LLM常识；
- 单一券商、评论或行业传闻；
- 主题相关性或Ticker热度；
- 股价上涨。

## 14. Golden Case 验收集合

v1 实现至少需要覆盖五类人工标注案例：

1. 真实的 `durable_chokepoint_owner`；
2. Defensibility仍高但已有 `credible_challenge` 的Contested Owner；
3. 市场份额高但不可替代性证据不足的当前龙头；
4. 技术或样品有进展但尚未达到 `replacement_ready` 的挑战者；
5. Segment是真卡点但公司为 `weak_earnings_capture` 的案例。

每个案例必须包括：

- 预期范围键；
- 人工标注的Claim和原文；
- 每个维度的允许等级或区间；
- 预期Coverage和证据质量；
- 预期最终状态；
- 至少一个反例或反证；
- 评分失败时的可解释差异。

Golden Case可以验证或推翻本合同，但修改合同必须产生新版本。

## 15. 版本变更规则

以下变化需要新合同版本：

- 增删评分维度；
- 修改权重；
- 修改0/2/4锚点；
- 修改Coverage、Score Width或计算公式；
- 修改准入或状态门槛；
- 修改成熟度顺序或语义；
- 修改证据质量和双向验证要求。

纯文字澄清、错别字修复或不改变结果的示例补充可以发布补丁版本，但必须记录变更说明。
