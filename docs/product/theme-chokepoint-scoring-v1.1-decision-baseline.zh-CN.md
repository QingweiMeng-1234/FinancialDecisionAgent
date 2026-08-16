# Theme Chokepoint Scoring v1.1 设计决策基线

## 1. 文档状态

| 字段 | 值 |
| --- | --- |
| 文档类型 | v1.1 设计决策记录 |
| 状态 | `accepted_decision_record_with_reopened_freeze_gate`；结构决策已并入v1.1候选，冻结结论已撤回 |
| 基于合同 | `theme-chokepoint-scoring-v1` |
| 当前合同候选 | `theme-chokepoint-scoring-contract-v1.1.zh-CN.md` |
| 边界回归 | `hbm-advanced-packaging-golden-regression-v1.1-2026-08-15.zh-CN.md` |
| 决策日期 | 2026-08-15 |
| 适用范围 | Theme Chokepoint Research Agent |

本文件保存产品负责人确认过的结构性决策及其形成过程。当前0–4锚点、权重、准入门槛和状态迁移以`theme-chokepoint-scoring-v1.1`设计候选为准；在跨主题正例、双标注和校准Gate通过前，它不是已批准实现规范。本记录不覆盖历史v1，也不承担评分器规范职责。

## 2. 已批准决策

### 2.1 分离 `evidence_state` 与 `bound_type`

v1.1 不新增顶层 `bounded` 状态。证据是否受支持/冲突与评分边界形状分别保存：

```yaml
evidence_state: supported | conflicted | unknown
bound_type: exact | lower_bound | upper_bound | interval | none
rating_min: 0..4
rating_max: 0..4
```

| `evidence_state` | `bound_type` | 区间语义 |
| --- | --- | --- |
| `supported` | `exact` | 同一范围和时点下，既证明达到该档，也有证据排除相邻档；`rating_min == rating_max` |
| `supported` | `lower_bound` | 至少达到 `rating_min`，更高档尚未被排除 |
| `supported` | `upper_bound` | 有直接证据证明不超过 `rating_max` |
| `supported` | `interval` | 不存在可信冲突，证据共同将结果限定在 `[rating_min, rating_max]` |
| `conflicted` | `interval` | 可信来源对同一原子事实存在实质冲突；区间保存各来源能够支持的边界 |
| `unknown` | `none` | 没有有效评分边界，默认 `[0,4]` |

`stale` 继续作为独立 freshness flag，不替代 `evidence_state` 或 `bound_type`。

通用规则：

- 缺少公开证据是 `unknown`，不是 0 分；
- 0 分必须有明确失败、退出、未采用、不符合要求、取消或其他负向事实；
- “最高完全证明档位”默认形成 `supported + lower_bound` 的 `rating_min`，不能自动写成精确点值；
- 模型置信度不能收窄证据区间。

v1.1 不再维护跨技术、认证、生产、采用、财务、份额和替代关系的单一 `maturity` 总序。每条轴分别使用本节的证据边界合同；完整多轴结构见 2.6。

### 2.2 Material Revenue 拆成三个字段

不再使用一个 OR 条件把公司、分部和市场口径合并为单一 `material_revenue=true`。

| 字段 | 回答的问题 | 主要使用位置 |
| --- | --- | --- |
| `issuer_materiality` | 产品收入是否对发行人整体重要 | Earnings Transmission |
| `segment_materiality` | 产品收入是否对稳定披露的所属分部重要 | Earnings Transmission |
| `market_presence` | 产品是否已形成有意义的目标市场规模 | Replacement/竞争状态 |

以下阈值已由v1.1候选采纳并补齐连续期间语义，但其排序校准仍属于重新冻结Gate：

```text
issuer_materiality candidate threshold: 产品收入 / 公司收入 >= 1%
segment_materiality candidate threshold: 产品收入 / 稳定分部收入 >= 5%
market_presence candidate threshold: 产品收入或出货 / 同口径目标市场 >= 5%
```

三个字段必须分别保存分子、分母、期间、币种/单位、gross/net、sell-in/end consumption 和来源。市场口径不得用于证明对公司收入重要。

财务里程碑与市场存在也必须分开：

```text
financial:
  no_revenue
  realized_unquantified
  single_period_material
  material_revenue
  recurring_material_revenue

share_trajectory:
  losing
  stable
  single_period_gain
  sustained_gain
```

其中 `no_revenue` 必须有明确无收入、退出或取消证据；没有收入披露仍是 `unknown`。`material_revenue` 只允许由 `issuer_materiality` 或 `segment_materiality` 证明；`market_presence` 不得用于证明该状态。

`sustained_gain` 至少需要三个可比时点、两个连续提升区间，并保持产品、地区、客户范围、期间、分母和份额定义一致。两个时点只能支持 `single_period_gain`。

### 2.3 `constraint_persistence` 移出 100 分

`constraint_persistence` 不再作为 Segment Chokepoint Score 的独立加权维度，避免把 Demand、Capacity、Qualification、Substitute 和 Inventory 再次重复计分。

v1.1 将其改为派生输出：

```text
relief_horizon_base
relief_horizon_stress
node_relief_date
system_relief_date
relief_confidence
constraint_transition_state
```

候选迁移状态至少区分：

```text
persisting
relieved
transferred
coexisting_constraints
demand_normalized
demand_destroyed
insufficient_evidence
```

Relief Horizon已在候选合同第12节落为库存—流量模型。库存必须按时间桶滚动，不能与月度产能直接静态相加；primary与alternative supply必须互斥去重。

### 2.4 单一主要高档归属，允许有限复用

同一文档可以产生多个原子 Claim。同一个原子事实可以为多个维度提供有限下限或上下文，但只能有一个 `primary_scoring_dimension` 用于满足高档评分条件。

```yaml
primary_scoring_dimension: customer_adoption
secondary_uses:
  - dimension: capacity_readiness
    role: floor_only
  - dimension: execution_funding
    role: context_only
```

允许的 Claim 使用角色：

| role | 语义 |
| --- | --- |
| `primary` | 该原子事实的主要计分归属；可以参与该维度高档条件判断 |
| `floor_only` | 只能形成其他维度的有限正向下限，不能单独满足该维度 3 分或 4 分条件 |
| `context_only` | 只解释背景、pipeline 或风险，不改变该维度评分边界 |

| 原子事实 | `primary` 归属 | 允许的有限复用 |
| --- | --- | --- |
| 送样、验证、正式批准、认证失败 | `qualification_progress` | 更新 Qualification 轴边界；不得给 Customer Adoption 高分 |
| 付费试产、生产使用、商业交付、重复生产使用 | `customer_adoption` | 商业出货可作为 Capacity 的 `floor_only`，证明存在非零可售产出 |
| 已确认外部产品收入及其公司/分部分母 | `issuer_materiality` 或 `segment_materiality` | 只能作为 Customer Adoption 的 `floor_only`；收入不得单独满足 Adoption 3/4 分条件 |
| 资金、设备订单、团队、约束性供应承诺 | `execution_funding` | 作为 Capacity pipeline 的 `context_only` |
| 试产、良率、稳定可售产出、交付余量 | `capacity_readiness` | Execution 历史记录需要独立 Claim，不复用当前项目产出形成高档 |
| 产品性能、功耗、热、质量和可靠性结果 | `performance_parity` | 作为技术门槛 context；制造良率只归 Capacity，不得在 Performance 重复计分 |
| 接口、工具、流程、认证迁移、停机 | `ecosystem_compatibility` | 作为 TCO 输入；不得证明产品性能达到要求 |
| 三个同口径时点、两个连续份额提升区间 | `share_trajectory` gate | Customer Adoption 可引用采用规模下限，但不得重复加权 |

任何维度达到 3 分或 4 分，都必须存在归属于该维度的独立 `primary` Claim；同一原子事实不能单独满足多个维度的高档条件。多轴里程碑只作为状态 Gate，不再作为额外加分项。

### 2.5 高召回池与确认状态分层

v1.1 必须区分：

| 状态 | 目的 |
| --- | --- |
| `review_candidate` | 高召回候选池，供人工复核；允许 `supported + lower_bound/upper_bound/interval` 和明确缺失问题 |
| `confirmed_candidate` | 证据门槛、评分下限、来源要求和冲突处理均通过的已确认候选 |

不得只按 `score_max` 排序，否则 unknown 最多的对象会获得不合理优先级。严格排序只在以下条件成立时允许：

```text
A.score_min > B.score_max
```

否则候选关系输出 `overlapping` 或 `no_order`。

### 2.6 使用带 Scope 的多轴里程碑账本

单一 `maturity` 枚举废弃为主状态。每条里程碑记录必须绑定完整评估范围：

```yaml
scope:
  company_id:
  product_id:
  segment_id:
  customer_or_platform_scope:
  geography:
  time_horizon:
  as_of_date:

milestones:
  product_readiness: concept | prototype | sample_ready
  qualification: not_started | testing | design_selected | qualified | production_approved
  production: none | pilot | ramp | mass_production | stable_scaled_output
  adoption: evaluation | paid_pilot | production_use | recurring_use | multi_platform_use
  financial: no_revenue | realized_unquantified | single_period_material | material_revenue | recurring_material_revenue
  share_trajectory: losing | stable | single_period_gain | sustained_gain
  displacement: none | partial | confirmed
```

每条轴同时保存 `evidence_state + bound_type + state_min/state_max + as_of_date`。业务状态未知时由 `evidence_state: unknown` 表达，不在业务枚举中增加一个可与已证状态混排的 `unknown` 档。

允许保留 `maturity_floor` 作为旧界面兼容展示，但它只能由多轴账本派生，且不得参与计分、状态 Gate 或跨 Scope 推断。量产不自动推出收入，收入不自动推出份额，份额增长不自动推出实际替代。

### 2.7 Replacement 硬门槛与状态语义

加权总分只能用于候选优先级，不能补偿关键条件失败。每个硬门槛派生：

```text
gate_state = pass | fail | unknown
```

通用派生规则：

- `pass`：对应维度为 `evidence_state: supported`，且 `rating_min` 达到阈值并通过 Scope、来源和时效要求；
- `fail`：存在同范围明确失败/不合格/退出/阻断证据，或 `supported` 的 `rating_max` 低于阈值；
- `unknown`：包括缺证、未完成反证搜索和未解决 `conflicted`；
- 禁止用 `rating_max >= threshold` 作为通过条件，因为 `unknown=[0,4]` 会错误通过。

Replacement 状态不是一条可以只靠总分升级的总序。v1.1 采用以下派生标签：

| 状态 | 必要条件 |
| --- | --- |
| `credible_challenge` | 至少一个 Performance、Qualification、Capacity 或 Adoption 的同范围正向 `primary` Claim；没有明确 target-scope disqualifier。它是高召回标签，不代表已确认可替代。 |
| `replacement_ready` | Performance、Qualification、Capacity、Ecosystem 四个硬门槛全部 `pass`；完成规定反证搜索，未发现明确致命TCO、监管或架构阻断。冻结阈值分别为`rating_min >= 2/3/2/2`。 |
| `production_alternative` | `replacement_ready`；目标客户/平台已实际生产使用；存在同范围合格可售产出。它不声称原供应商已被替代。 |
| `scaled_alternative` | `production_alternative`；持续商业采用或达到待冻结的目标需求覆盖门槛。它可以来自市场增量，不声称发生 displacement。 |
| `realized_replacement` | `production_alternative`；且 `actual_displacement_evidence.evidence_state == supported`。收入、量产或市场增长不能替代该 Gate。 |
| `scaled_replacement` | `realized_replacement`；且规模 Gate 通过。 |

`confirmed_candidate` 进入任何带“ready/production/scaled/realized”的确认状态时，所有规定硬门槛必须为 `pass`；`unknown` 或 `conflicted` 不能通过。`review_candidate` 可以保留硬门槛为 `unknown` 的高上行对象，但必须列出对应 `missing_material_questions`。

业务状态同时输出累计 Gate 和唯一主状态：

```yaml
business_state:
  achieved_gates:
    - credible_challenge
    - replacement_ready
    - production_alternative
  primary_state: production_alternative
```

- `achieved_gates[]` 保存审计轨迹，不丢失对象已通过的底层条件；
- `primary_state` 用于列表、筛选和人工审核，必须唯一且由规则派生，模型不得自由选择；
- Replacement 的确定优先级为：

```text
scaled_replacement
> realized_replacement
> scaled_alternative
> production_alternative
> replacement_ready
> credible_challenge
> early_signal
```

若对象已通过 scale Gate 和 displacement Gate，必须派生为 `scaled_replacement`；不得在 `scaled_alternative` 与 `realized_replacement` 之间任意选择一个主状态。

### 2.8 `actual_displacement_evidence` 合同

`realized_replacement` 必须证明同一个 `assessment_scope` 内发生了实际供应迁移。客户可以不公开具名，但原文必须稳定锁定同一客户/项目、产品或平台范围和期间。

允许两类证明路径：

1. **直接证据**：客户或供应商明确确认替换、移除、供应份额迁移、second-source 转主供或设计路线切换；
2. **严格三角验证**：同产品和同分母下至少三个可比时点，incumbent 份额下降且 challenger 份额上升，期间对齐，并有原文足以排除“只因总市场增长而双方都扩张”的解释。

建议字段：

```yaml
actual_displacement_evidence:
  evidence_state: supported | conflicted | unknown
  method: direct | triangulated | none
  incumbent_id:
  challenger_id:
  customer_or_program_scope:
  product_or_platform_scope:
  comparison_periods: []
  claim_ids: []
  counter_explanations_checked: []
```

`material_revenue`、`market_presence`、`sustained_gain` 和 `mass_production` 可以证明规模或商业化，但任何一个都不能单独证明 displacement。

### 2.9 证据、业务和趋势三轴分离

v1.1 批准三轴模型，但不新增与 `evidence_state + bound_type` 重复的顶层证据状态：

```yaml
evidence_axis:
  evidence_state: supported | conflicted | unknown
  bound_type: exact | lower_bound | upper_bound | interval | none
  freshness_state: current | stale

business_axis:
  achieved_gates: []
  primary_state:

trend_axis:
  change_type: knowledge_revision | industry_event
  trend_state: strengthening | weakening | invalidated | transferred | coexisting_constraints | unchanged
```

`lower_bounded`、`upper_bounded` 等展示状态只能从 `evidence_state + bound_type` 派生，不得另存一套可能漂移的 source of truth。

监控层必须区分：

```text
knowledge_revision  # 新找到或重新解释了旧事实
industry_event      # 产业在事件发生时间发生了真实变化
```

`knowledge_revision` 可以改变当前证据边界、Coverage 和回溯快照，但不能仅因 `score_min` 上升就输出 `strengthening`。`strengthening/weakening/transferred` 必须绑定 `industry_event.event_time`、可比 Scope 和真实业务状态变化。`unknown` 或 `stale` 只能改变证据轴，不能单独触发 `invalidated`。

### 2.10 TCO 与 Ecosystem 的计分边界

当 TCO 使用完整生命周期口径并包含认证、接口改造、工具迁移、流程变化、停机和切换成本时，`ecosystem_compatibility` 不再作为 100 分中的加权维度，避免相同迁移事实重复计分。

| 字段 | 回答的问题 | 合同角色 |
| --- | --- | --- |
| `ecosystem_compatibility` | 技术和流程上能否迁移，需要改变什么 | 非加权硬门槛；输出 `pass/fail/unknown` 和诊断子项 |
| `cost_tco_advantage` | 完整迁移加运营的经济性是否有优势 | 保留 0–4 加权评分；无完整可比数据时为区间或 `unknown` |

`replacement_ready`必须要求Ecosystem Gate为`pass`。`review_candidate`允许该Gate为`unknown`，但必须列出待核验的接口、工具、认证、流程、停机和架构问题。Ecosystem已退出100分并保留为非加权硬门槛；Replacement当前候选权重见合同第7.1节。

### 2.11 Criticality、Concentration 与 Substitute 的边界

`downstream_criticality` 只回答目标 Segment 不可用时的原始后果，包括交付延迟、成本、性能、投产和合规影响；其锚点不得再使用“无可行绕行”或“没有替代路线”作为高分条件。

`substitute_weakness` 只回答不同产品、工艺、架构或供应路线能否缓解该后果，包括准备时间、合格产出和需求覆盖比例。

```text
Downstream Criticality = 断掉会有多严重
Substitute Weakness    = 有没有其他路线补上
```

`effective_supply_concentration` 只计算同规格、同认证路径下的 like-for-like 合格供应商集中度和有效余量；普通第二供应商属于 Concentration，不同技术路线属于 Substitute Weakness。

Criticality最终允许由同范围客户、工程或合规原文直接证明“会阻止出货、投产、合规或关键性能”而达到4分，不要求事故已经发生；普通推演或无直接阻断机制时最高3分。替代能力不得重新塞回Criticality。

### 2.12 四个状态族的 `primary_state` 优先级

以下规则记录已批准的状态求值方向。具体分数、Coverage数值和资格阈值已进入候选合同，但仍需跨主题校准后才能冻结。所有状态族先计算证据资格，再计算业务状态：

```yaml
state_assignment:
  eligible: true | false
  withheld_reason: null | insufficient_evidence | unresolved_conflict | stale_scope | incomplete_search
  achieved_gates: []
  primary_state: null
```

当 `eligible == false` 时，`primary_state` 必须为 `null`，并填写 `withheld_reason`。`insufficient_evidence`、`conflicted` 和 `stale` 属于证据轴，不再与业务状态竞争 `primary_state`。

任何对象若同时命中两个按定义应互斥的分支，评分器必须输出 `state_contract_error` 并停止发布该卡，不得依靠数组顺序静默选一个状态。

#### 2.12.1 Segment

Segment 业务状态只允许：

```text
not_supported
watch_segment
candidate_chokepoint
strong_candidate_chokepoint
```

确定求值顺序：

```text
IF segment_state_eligible == false
  primary_state = null
ELSE IF segment_hard_fail == true
  primary_state = not_supported
ELSE IF strong_candidate_gate == pass
  primary_state = strong_candidate_chokepoint
ELSE IF candidate_gate == pass
  primary_state = candidate_chokepoint
ELSE IF watch_gate == pass
  primary_state = watch_segment
ELSE IF resolved_upper_bounds_exclude_watch == true
  primary_state = not_supported
ELSE
  state_contract_error
```

`segment_hard_fail` 必须来自同范围受支持的负向事实或上限，例如 Demand/Criticality 明确不足；unknown 不能触发。`strong_candidate_chokepoint` 的 `achieved_gates[]` 同时保留 `watch_segment` 和 `candidate_chokepoint`。

#### 2.12.2 Defensibility

Defensibility 业务状态只允许：

```text
low_defensibility
medium_defensibility
high_defensibility
```

`defensibility_state_eligible` 至少要求 Scope、时效和 Coverage Gate 通过，且强制客户采购/切换证据不是 `unknown` 或未解决 `conflicted`。未通过时 `primary_state = null`，不得同时输出 `medium_defensibility` 和 `defensibility_uncertain`。

确定求值顺序：

```text
IF defensibility_state_eligible == false
  primary_state = null
ELSE IF high_defensibility_gate == pass
  primary_state = high_defensibility
ELSE IF low_defensibility_gate == pass
  primary_state = low_defensibility
ELSE
  primary_state = medium_defensibility
```

High 与 Low 的评分区间必须互斥；若两者同时通过则为 `state_contract_error`。`medium_defensibility` 是证据充分后的中间业务结论，不是“不知道”。

#### 2.12.3 Competition

公司竞争状态只允许：

```text
durable_chokepoint_owner
contested_chokepoint_owner
differentiated_incumbent
vulnerable_incumbent
commodity_or_weakly_differentiated
```

进入 Competition 状态前必须有：

```yaml
competition_state_eligible: true
incumbent_defensibility_primary_state: high | medium | low
challenger_set:
  challenger_set_id:
  assessment_scope:
  search_protocol_version:
  search_completed_at:
  as_of_date:
  coverage_gate: pass
```

“所有挑战者”只能解释为上述冻结 Scope、检索协议和截止时间内的 `challenger_set`，不得声称证明开放世界中不存在未知挑战者。

确定求值顺序：

```text
IF competition_state_eligible == false
  primary_state = null
ELSE IF exists challenger >= realized_replacement
  primary_state = vulnerable_incumbent
ELSE IF defensibility == low
        AND exists challenger >= replacement_ready
  primary_state = vulnerable_incumbent
ELSE IF defensibility IN {high, medium}
        AND exists challenger >= credible_challenge
  primary_state = contested_chokepoint_owner
ELSE IF defensibility == high
        AND no challenger >= credible_challenge
  primary_state = durable_chokepoint_owner
ELSE IF defensibility == medium
        AND no challenger >= credible_challenge
  primary_state = differentiated_incumbent
ELSE IF defensibility == low
  primary_state = commodity_or_weakly_differentiated
ELSE
  state_contract_error
```

任何受支持的 `realized_replacement` 都优先触发 `vulnerable_incumbent`，因为同范围实际 displacement 不能被在位者历史防御分补偿。尚未发生 displacement 时，`vulnerable_incumbent` 优先于 Commodity，因为已存在可执行替代威胁；`contested_chokepoint_owner` 优先于 Durable，因为任何可信挑战者都会否定“当前无可信挑战”的业务断言。Low Defensibility 但挑战者只达到 `credible_challenge`、尚未 ready 时，主状态仍为 Commodity，同时在 `achieved_gates[]` 保留挑战者 Gate。

#### 2.12.4 Earnings

Earnings 业务状态只允许：

```text
weak_earnings_capture
moderate_earnings_path
material_earnings_path
```

`earnings_state_eligible` 至少要求财务 Scope、期间和分母可比，Coverage Gate 通过，并且 Revenue Materiality、Time to Revenue、Margin Transmission 三个强制字段不存在 `unknown` 或未解决 `conflicted`。

确定求值顺序：

```text
IF earnings_state_eligible == false
  primary_state = null
ELSE IF earnings_hard_fail == true
  primary_state = weak_earnings_capture
ELSE IF material_earnings_gate == pass
  primary_state = material_earnings_path
ELSE
  primary_state = moderate_earnings_path
```

`earnings_hard_fail` 包括同范围受支持的 Revenue Materiality、Time to Revenue 或 Margin Transmission 上限低于最低门槛，以及足以否定期限内利润捕获的资本/现金负担。`material_earnings_path` 必须同时通过收入、兑现时间和利润传导 Gate，不能只证明收入路径。`moderate_earnings_path` 是证据充分后的中间业务状态；关键财务字段 unknown 时必须 withheld，而不是 Moderate。

`material_earnings_gate` 必须显式包含 `earnings_hard_fail == false`。若实现同时计算出 Hard Fail 和 Material Gate 通过，必须输出 `state_contract_error`，不能让总分补偿利润传导失败。

#### 2.12.5 强制反例

| 输入反例 | 唯一允许输出 | 必须拒绝 |
| --- | --- | --- |
| Segment 全维度 unknown，`score_max=100` | `eligible=false`, `primary_state=null`, `withheld_reason=insufficient_evidence` | `watch_segment` |
| Defensibility Coverage看似充足，但客户采购/切换证据 unknown | `eligible=false`, `primary_state=null` | `medium_defensibility` 与 `defensibility_uncertain` 同时输出 |
| Low Defensibility，存在 `replacement_ready` 挑战者 | `primary_state=vulnerable_incumbent` | 同时把 Commodity 作为另一个主状态 |
| High Defensibility，但存在受支持的 `realized_replacement` | `primary_state=vulnerable_incumbent` | 用历史防御总分覆盖实际 displacement，输出 Durable 或 Contested |
| Medium Defensibility，冻结挑战者集合内没有 `credible_challenge` | `primary_state=differentiated_incumbent` | `durable_chokepoint_owner` |
| High Defensibility，但挑战者搜索未完成 | `eligible=false`, `primary_state=null`, `withheld_reason=incomplete_search` | `durable_chokepoint_owner` |
| Earnings收入和兑现时间通过，但 Margin Transmission unknown | `eligible=false`, `primary_state=null` | `material_earnings_path` |
| Earnings总分很高，但 Margin Transmission存在受支持的硬失败 | `primary_state=weak_earnings_capture` | `material_earnings_path` |

### 2.13 区间运算与 Coverage 合同

#### 2.13.1 合法状态组合

Claim 级评分边界只允许以下组合：

| `evidence_state` | `bound_type` | `rating_min` | `rating_max` | 语义 |
| --- | --- | ---: | ---: | --- |
| `supported` | `exact` | `k` | `k` | 同范围证据同时支持达到该档并排除其他档 |
| `supported` | `lower_bound` | `k` | `4` | 至少达到 `k`，更高档未被排除；`k`必须大于0且小于4 |
| `supported` | `upper_bound` | `0` | `k` | 不超过 `k`；`k`必须大于0且小于4 |
| `supported` | `interval` | `a` | `b` | 证据同时给出有效上下界；`0 < a < b < 4` |
| `conflicted` | `interval` | `a` | `b` | 同一原子事实存在未解决可信冲突；同时保存冲突分支 |
| `unknown` | `none` | `0` | `4` | 没有有效评分边界 |

边界退化时必须规范化：`lower_bound`的 `k=4`、`upper_bound`的 `k=0` 都改为 `exact`；没有分支信息的 `[0,4]` 改为 `unknown + none`。`conflicted` 可以使用 `[0,4]` 冲突包络，但必须同时保存至少两个信息性、互不相容的 `feasible_intervals[]`，例如 `[0,0]` 与 `[4,4]`。

以下组合为 `evidence_contract_error`：

- `supported + none`；
- `unknown` 但区间不是 `[0,4]`；
- `exact` 但 `rating_min != rating_max`；
- `lower_bound` 但 `rating_max != 4`；
- `upper_bound` 但 `rating_min != 0`；
- `rating_min > rating_max`；
- 只有“未找到更高证据”却生成 `upper_bound`；
- 只有“未找到失败证据”却生成正向 `lower_bound`。

0分仍必须由明确负向事实支持。搜索完成但未找到证据，只能记录检索覆盖，不能产生0分或其他业务边界。

#### 2.13.2 Claim进入维度运算的资格

只有同时满足以下条件的Claim才能改变维度区间：

```text
scope完全匹配
AND as_of/有效期间满足要求
AND freshness_state == current
AND scoring_eligible == true
AND scoring_role IN {primary, floor_only}
```

- `context_only` 不参与区间运算；
- `stale` Claim 保留在历史账本，但不进入当前区间；
- `unknown` Claim 是无约束项，不得收窄 `[0,4]`；
- `floor_only` 只能提高有限正向下限，不能生成上限，且其有效下限最高为2分；
- 任何3分或4分下限必须至少有一条该维度的 `primary` Claim。

#### 2.13.3 无冲突聚合

每条合格 Claim 先转换成闭区间 `I_c=[min_c,max_c]`。同一维度、同一 Scope 和同一快照的无冲突区间使用交集：

```text
dimension_interval = intersection(I_1, I_2, ..., I_n)
rating_min = max(min_1, min_2, ..., min_n)
rating_max = min(max_1, max_2, ..., max_n)
```

聚合结果按端点规范化：

```text
[k,k]   -> exact
[k,4]   -> lower_bound
[0,k]   -> upper_bound
[a,b]   -> interval
[0,4]   -> unknown + none
```

新证据在相同 Scope/时点下只能保持或收窄区间。若更新导致区间扩大，必须记录为 Claim撤销、来源更正、Scope变化或新时间快照，不能伪装成普通追加证据。

若不同 `fact_key` 的无冲突 Claim 交集为空，说明评分映射或Scope合同自相矛盾，输出 `evidence_mapping_error`。只有同一原子事实的可信来源互相矛盾，才能使用 `conflicted`。

#### 2.13.4 冲突聚合

冲突必须保存分支，不能只保存一个覆盖所有可能值的大区间：

```yaml
evidence_state: conflicted
bound_type: interval
rating_min: 1
rating_max: 4
feasible_intervals:
  - [1, 1]
  - [4, 4]
conflict_set_id:
conflicting_claim_ids: []
conflict_reason:
```

`rating_min/rating_max` 是便于排序和展示的冲突包络，`feasible_intervals[]` 才是实际可行分支。其他无冲突约束必须分别与每个分支求交；空分支被移除。若最终所有分支映射到同一结果，可关闭该维度冲突；否则维度保持 `conflicted`。

建立 `conflict_set_id` 至少需要两个同Scope、同事实、同时期且达到最低来源质量的独立 Claim。来源层级不同但内容并不真正矛盾、只是一个更具体时，应优先做Scope或时间消歧，不得为了扩大区间标成 `conflicted`。

来源只是措辞模糊、Scope不清或转录有歧义时，不得伪造两个冲突立场。应标记 `source_ambiguity/human_review_required`，语义 Claim 保持 `unknown`。

#### 2.13.5 分数区间

对当前激活的加权维度集合 `D`：

```text
score_min = Σ(weight_d * rating_min_d / 4)
score_max = Σ(weight_d * rating_max_d / 4)
```

分数区间只用于排序边界和业务Gate输入，不代表区间内部概率均匀。`score_max` 不能单独生成 `review_candidate`，也不能通过强制硬门槛。

硬门槛统一使用：

```text
pass    = evidence_state == supported AND rating_min >= threshold
fail    = evidence_state == supported AND rating_max < threshold
unknown = 其他情况，包括conflicted
```

#### 2.13.6 Coverage拆分

v1.1 废弃含义模糊的单一 `evidence_coverage`，输出：

```text
presence_coverage
resolved_coverage
decision_coverage
conflicted_weight_share
unknown_weight_share
stale_weight_share
```

Coverage 分母是当前激活的加权维度权重之和，不包括 Ecosystem 等非加权硬门槛；所有硬门槛另行逐项检查，不能被Coverage补偿。

对每个加权维度 `d`：

```text
presence_credit(d):
  supported  -> 1
  conflicted -> 1
  unknown    -> 0

resolved_credit(d):
  supported  -> 1
  conflicted -> 0
  unknown    -> 0

resolution_credit(d):
  1 - (rating_max_d - rating_min_d) / 4

decision_credit(d):
  resolved_credit(d) * resolution_credit(d)
```

聚合公式：

```text
presence_coverage = Σ(weight_d * presence_credit_d) / Σ(weight_d)
resolved_coverage = Σ(weight_d * resolved_credit_d) / Σ(weight_d)
decision_coverage = Σ(weight_d * decision_credit_d) / Σ(weight_d)
```

缺口份额：

```text
conflicted_weight_share = Σ(weight_d * 1[evidence_state_d == conflicted]) / Σ(weight_d)
unknown_weight_share    = Σ(weight_d * 1[evidence_state_d == unknown]) / Σ(weight_d)
stale_weight_share      = Σ(weight_d * 1[current evidence is absent and stale historical evidence exists]) / Σ(weight_d)
```

`stale_weight_share` 是 `unknown_weight_share` 的诊断子集，因此这些字段不要求相加等于1。

因此：

- `supported + exact` 的 Decision Credit 为1；
- `supported + lower_bound [2,4]` 的 Decision Credit 为0.5；
- `supported + interval [1,3]` 的 Decision Credit 为0.5；
- `conflicted` 可以提高 Presence Coverage，但在Resolved和Decision Coverage中计0；
- `unknown` 和当前已过期证据在三种当前决策Coverage中计0；
- `stale_weight_share` 单独展示历史证据过期造成的缺口。

`review_candidate`可以使用Presence Coverage做研究工作量和来源覆盖排序，但必须先通过3.1的最低正向证据。`confirmed_candidate`和所有确认业务状态只能使用Decision Coverage，并要求各强制Gate单独`pass`。当前候选阈值见合同第5–10节。

#### 2.13.7 Coverage强制不变量

```text
0 <= decision_coverage <= resolved_coverage <= presence_coverage <= 1
```

| 单一等权维度场景 | Presence | Resolved | Decision |
| --- | ---: | ---: | ---: |
| `unknown [0,4]` | 0 | 0 | 0 |
| `conflicted`, 包络 `[0,4]` | 1 | 0 | 0 |
| `supported + lower_bound [2,4]` | 1 | 1 | 0.5 |
| `supported + interval [1,3]` | 1 | 1 | 0.5 |
| `supported + exact 0`，有明确失败证据 | 1 | 1 | 1 |
| `supported + exact 4` | 1 | 1 | 1 |

Coverage只表示证据存在、冲突解决和区间分辨率，不表示业务好坏。因此受支持的明确0分与明确4分具有相同Coverage。

## 3. `review_candidate` 容量合同

每个 Theme 最多进入 10 张 scoped candidate card。

候选卡的工作量单位不是公司名称，而是：

```text
company_id
+ product_id
+ segment_id
+ customer_scope/region
+ time_horizon
+ as_of_date
```

同一公司跨产品或 Segment 时分别计数。

### 3.1 最低正向证据

高召回不等于保留全 unknown 对象。`score_max` 不能单独产生 `review_candidate` 资格。

Segment 普通候选和高上行候选至少满足：

```text
assessment_scope 完整
AND demand_pressure.rating_min >= 1
AND downstream_criticality.rating_min >= 1
AND 以下至少一个供应摩擦维度 rating_min >= 2：
    effective_supply_concentration
    qualification_barrier
    capacity_inelasticity
    substitute_weakness
AND 至少一条直接需求/客户侧 primary Claim
AND 至少一条直接供应侧 primary Claim
```

Replacement 普通候选至少满足：

```text
assessment_scope 完整
AND 至少一个产品级正向 primary Claim
AND 该 Claim 主要归属于以下至少一轴：
    product_readiness
    performance_parity
    qualification_progress
    production
    capacity_readiness
    customer_adoption
AND Performance Gate != fail
```

政策利好、融资、CapEx、设备订单、市场预测或公司路线图不能单独产生 Replacement 候选资格。

两个“高上行空间”名额允许只有一个强产品级正向下限，但必须同时具备明确目标产品及客户/平台 Scope，并列出能够收窄关键 Gate 的 `missing_material_questions`。

两个“反例/迁移”名额可以包含失败或被替代对象，但必须有明确负向事实、卡点迁移或 false-leader 证据；全 unknown 对象仍不得进入。

### 3.2 名额分配

| 类型 | 名额 | 目的 |
| --- | ---: | --- |
| 证据下限领先 | 6 | `proven_floor` 较高、证据相对完整的对象 |
| 高上行空间 | 2 | 已有强正向事实，但仍为 `lower_bound` 或非精确 `interval` 的对象 |
| 反例/迁移 | 2 | 挑战者、false leader、潜在卡点迁移或反证对象 |

### 3.3 多样性约束

- 同一 Segment 原则上最多占 5 张卡；
- 每个被识别为重要节点的 Segment 至少保留 1 张卡；
- `score_max=100` 但没有最低正向事实的对象不得只因 unknown 较多进入候选池；
- 每张非精确边界候选卡必须列出能够收窄区间的具体 `missing_material_questions`。

## 4. 已关闭的设计开放项

本记录原有11项结构设计问题均已形成候选答案：

1. 四套完整0–4锚点和各100分权重已形成候选，线性排序效果待跨主题校准；
2. `constraint_persistence`改为派生Relief Horizon，`ecosystem_compatibility`改为Replacement非加权硬门槛；
3. Presence/Resolved/Decision Coverage公式已确定，各状态阈值待重新冻结；
4. Regulatory与Architecture已拆为两个5分维度；
5. Effective Supply Concentration和Qualified Effective Capacity的组件算法及无重叠区间已形成候选；
6. Criticality的4分原文标准已确定，且继续与替代能力分离；
7. Substitute按同范围路线的去重可实现覆盖聚合；
8. 10卡预算、`6+2+2` lane、回流、Segment上限和最终tie-breaker已形成候选；
9. `knowledge_revision`与`industry_event`的趋势优先级和时间字段已确定；
10. Relief Horizon的库存—流量、需求、服务水平和卡点迁移模型已确定；
11. 四个状态族的资格、Hard Gate、唯一`primary_state`和优先级已形成候选。

## 5. 重新打开的冻结Gate

HBM/先进封装Golden边界回归已经通过，但主要验证“不要过度升级”，不能证明正向状态召回、跨主题排序或标注一致性。`theme-chokepoint-scoring-v1.1`因此恢复为`freeze_candidate`。

重新冻结前还必须完成：复合锚点谓词级条件覆盖、`origin_event_id/evidence_family_id`去重、15条HBM边界断言、AI数据中心电力/冷却跨主题正例、双标注盲评、分歧裁决、状态混淆矩阵及Top-10/成对排序校准。具体Gate见候选合同第17.1节。

候选阶段的语义修订必须写入决策记录、更新哈希并重跑全部回归，不能静默修改。未来一旦正式冻结，新增来源或Claim但不改变语义时只升级Golden/数据版本；改变维度、权重、锚点、Gate、Coverage、Scope、状态优先级、候选容量、Materiality/Share/Displacement/Relief或Claim复用规则时，必须创建新合同版本。
