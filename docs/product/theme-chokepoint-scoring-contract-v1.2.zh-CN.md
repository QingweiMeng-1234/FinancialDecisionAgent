# Theme Chokepoint Scoring Contract v1.2

## 1. 合同状态

| 字段 | 值 |
| --- | --- |
| contract_id | `theme-chokepoint-scoring-v1.2` |
| 状态 | `freeze_candidate`；v0.3预裁决一致性门失败，必须通过全新v0.4 Holdout后才可冻结 |
| 决策日期 | 2026-08-16 |
| 适用产品 | Theme Chokepoint Research Agent |
| 候选前版 | `theme-chokepoint-scoring-v1.1`；v1.1保持不可变，不被本候选回写 |
| 决策记录 | `theme-chokepoint-scoring-v1.2-migration-and-freeze-plan.zh-CN.md` |
| Golden基线 | `hbm-advanced-packaging-golden-claim-set-v0.1` + `v0.2`修订层 |
| 失败诊断 | `outputs/theme-chokepoint-holdout-v0.3-20260816-001/adjudication-and-freeze-decision-v0.3.zh-CN.md` |

本文是v1.2设计候选中唯一的评分语义来源，但在重新冻结前不得被称为已批准实现基线。PRD、v1、v1.1、旧Golden或讨论稿与本文候选语义冲突时，以本文记录当前候选；实现授权仍以重新冻结Gate为准。v1.1以及v0.1/v0.2/v0.3盲标结果均为不可变历史记录。

本文不构成投资建议，不输出目标价、买卖建议或股票预期收益。

## 2. 合同目标与评分对象

系统回答四个相互独立的问题：

1. `Segment Chokepoint`：供应链节点是否构成真实卡点；
2. `Company Defensibility`：在位公司为什么可能难以替代；
3. `Replacement Momentum`：挑战者是否具备替代能力，是否已经发生替代；
4. `Earnings Transmission`：卡点或替代能否传导到公司收入、利润和现金回报。

四套分数不得相互替代。公司规模、市场份额和龙头叙事不能直接证明防御性；技术成功不能直接证明利润；收入不能直接证明实际供应迁移。

每个评估对象必须绑定：

```text
company_id（公司评估时）
+ product_id
+ segment_id
+ customer_or_platform_scope
+ geography
+ time_horizon
+ as_of_date
```

Scope任一关键字段不兼容时必须拆卡，禁止跨产品、客户、平台、地区或时期拼接评分。`product_id`必须标识一个原子产品代际；当前产品、继任产品、内部测试芯片、已撤回产品和未来路线图必须分别建卡。任何`current_product OR successor_product`复合Scope均为schema错误，不进入评分。

## 3. Claim与证据合同

### 3.1 原文要求

最终计分Claim必须保存：

```yaml
claim_id:
fact_key:
origin_event_id:
evidence_family_id:
assessment_scope:
primary_scoring_dimension:
scoring_namespace: segment | defensibility | replacement | earnings | earnings_overlay | diagnostic
secondary_uses: []
condition_ids: []
evidence_state: supported | conflicted | unknown
bound_type: exact | lower_bound | upper_bound | interval | none
rating_min: 0..4
rating_max: 0..4
source_ids: []
exact_quote:
page_or_section:
publisher:
source_type:
publication_date:
data_as_of:
retrieved_at:
freshness_state: current | stale
evidence_time_semantics: current_actual | completed_historical | forward_commitment | management_target | mixed
source_ambiguity: false
human_review_required: false
scoring_eligible: true
```

搜索摘要、向量chunk、模型转述和无可复现页面只能用于发现来源，不能成为最终证据。结果型维度的0分必须有明确失败、退出、取消、未采用、不合格或其他负向原文；进度型维度允许同范围、当前有效原文明示`not_started/尚未开始/可售产出为零`支持0分。缺公开证据仍为`unknown`，不得把“未找到已开始”改写成“明确未开始”。

### 3.2 Claim使用角色

| role | 权限 |
| --- | --- |
| `primary` | 可以满足所属维度3/4分条件 |
| `floor_only` | 只能形成其他维度不高于2分的有限下限，不能生成上限 |
| `context_only` | 不改变评分边界 |

同一原子事实只有一个`primary_scoring_dimension`。一个文档可拆出多个不同`fact_key`的原子Claim。

Schema只接受合同中声明的规范字段名。Earnings加权字段唯一名称为`capital_cash_burden`；`capital_intensity`只允许在合同外迁移日志中记录为旧别名，进入评分payload时必须拒绝并返回`undeclared_dimension_name`，不得静默映射。

`earnings_overlay`是独立、非加权诊断命名空间，不属于Segment六个加权维度，也不进入Segment总分或Coverage分母。原O85/O86类财务诊断必须声明`scoring_namespace=earnings_overlay`或映射到正式Earnings维度，禁止声明`score_family=segment`。

### 3.2A 复合锚点的谓词级条件合同

一个0–4锚点包含多个条件时，必须先拆成可审计谓词，禁止直接从整段叙述生成维度分：

```yaml
condition_id:
dimension:
anchor_level: 0..4
predicate_id:
predicate_role: required | alternative | exclusion
predicate_group_id:
assessment_scope:
evidence_state: supported | conflicted | unknown
bound_type: exact | lower_bound | upper_bound | interval | none
claim_ids: []
origin_event_ids: []
evidence_family_ids: []
freshness_state: current | stale
```

求值规则：

1. `required`谓词必须全部在同一Scope和有效期间内为supported；缺一项时，该复合档不能形成下限；
2. 同一`predicate_group_id`的`alternative`分支至少完整通过一条，不能把两条不完整分支拼成一条完整分支；
3. `exclusion`谓词必须有直接证据排除约定的致命反例；搜索未发现不能当作排除证据；
4. 某档全部谓词通过只形成该档下限；只有另有受支持上界排除更高档时才能形成exact；
5. 不同公司、产品、客户/平台、地区、时间、分母或metric definition的谓词不得拼接；
6. 同一原文span若明确包含多个事实，可以拆成多个`fact_key`，但每个事实仍遵守单一主要高档归属。

输出`required_condition_count`、`resolved_required_condition_count`和逐谓词状态，用于解释缺口。`condition_coverage`只表示条件解析进度，不能按比例通过锚点、Hard Gate或业务状态。

### 3.2B 来源事件与证据家族去重

- `origin_event_id`标识产生事实的原始披露或业务事件，例如同一场业绩会、同一监管决定或同一客户采购迁移；
- `evidence_family_id`标识依赖同一原始披露的来源家族。新闻稿、电话会文字稿、媒体转载和聚合页若都只转述同一声明，不能冒充独立交叉验证；
- 同一事件可以支持多个明确的原子事实，但不能因页面数、段落数或转载数增加独立来源计数；
- “两个来源/两个期间/两个客户或平台”的锚点必须使用满足对应时间或主体条件的不同事件；若还要求来源独立，则`evidence_family_id`也必须不同；
- 来源家族归属不清时标记`source_ambiguity=true`并进入人工复核，不能默认独立。

### 3.2C 当前事实与未来承诺

- `current_actual`只接受截至`as_of_date`已经发生、正在生产使用、已经确认收入或已经形成合格可售产出的事实；
- `forward_commitment`包括将部署、计划爬坡、未来GW、未来产能和未来客户导入；它可以支持Execution或时间诊断，但不能自动证明当前Capacity、Production Adoption、Revenue或Displacement；
- `management_target`只表示目标，不表示有约束承诺；
- 同一原文同时包含“正在部署”和“未来承诺”且不能拆出清晰数量、客户或时间边界时，标记`mixed`。`mixed`证据最多支持语句中可独立成立的最低档，不得把未来规模回填到当前状态；
- `completed_historical`可以证明历史里程碑和持续期，但只有在当前仍有效且未被撤销时才能满足当前Gate；
- 当前产品与未来继任产品必须拆卡。`being deployed`、未来GW承诺或ramp计划本身不得证明当前合格产出、当前生产采用或已实现收入。

### 3.3 合法区间

| evidence_state | bound_type | 合法区间 |
| --- | --- | --- |
| `supported` | `exact` | `[k,k]` |
| `supported` | `lower_bound` | `[k,4]`，`0<k<4` |
| `supported` | `upper_bound` | `[0,k]`，`0<k<4` |
| `supported` | `interval` | `[a,b]`，`0<a<b<4` |
| `conflicted` | `interval` | 冲突包络，并保存至少两个`feasible_intervals[]` |
| `unknown` | `none` | `[0,4]` |

无分支信息的`[0,4]`必须是`unknown`。`conflicted [0,4]`只有在保存例如`[0,0]`与`[4,4]`等信息性冲突分支时合法。

区间状态按以下确定性顺序求值，标注员不得按主观置信度选择边界类型：

1. 先逐档求值完整锚点谓词；只有一个档位的全部required/alternative条件都受支持，才得到该档受支持下限；
2. `exact [k,k]`要求同时存在`k`档下限，以及直接原文或合同排他关系形成的上界，明确排除全部`>k`档；`exact 0`必须有明确失败、退出、未采用、不合格或可售产出为零证据；`exact 4`在4档全部谓词通过后自然封顶；
3. `lower_bound [k,4]`用于完整满足`k`档，但更高档至少一项必要谓词仍未解析，且没有受支持上界；
4. `upper_bound [0,k]`要求直接反证排除全部`>k`档；未搜到更高档证据不能形成上界；
5. 同时存在受支持下限`a`与上界`b`且`0<a<b<4`时输出`interval [a,b]`；
6. 没有任何完整锚点谓词得到支持，且不存在直接可用上界时，输出`unknown + none + [0,4]`；零散语句、管理层愿景和搜索缺口不得形成虚假有限区间；
7. `conflicted`仅用于同一`fact_key`、同一Scope、同一有效期内的可信来源实质冲突；官方后续recast或更正是带lineage的新版本覆盖旧口径，不自动构成source conflict。

### 3.4 聚合

只有Scope匹配、当前有效、`scoring_eligible=true`且role为`primary/floor_only`的Claim参与运算。无冲突Claim使用闭区间交集：

```text
rating_min = max(claim.rating_min)
rating_max = min(claim.rating_max)
```

不同`fact_key`导致空交集时输出`evidence_mapping_error`；同一`fact_key`的可信来源矛盾才能输出`conflicted`。冲突必须逐分支与其他约束求交，不能把包络误当连续可行区间。

来源措辞模糊或转录有歧义时，使用`source_ambiguity=true`、`human_review_required=true`，语义Claim保持`unknown`，不能伪造成来源冲突。

### 3.5 三轴状态

```yaml
evidence_axis:
  evidence_state:
  bound_type:
  freshness_state:

business_axis:
  eligible: true | false
  withheld_reason: null | insufficient_evidence | unresolved_conflict | stale_scope | incomplete_search
  achieved_gates: []
  primary_state:

trend_axis:
  change_type: knowledge_revision | industry_event
  trend_state:
```

`eligible=false`时`primary_state=null`。证据不足、冲突和过期不是业务状态。

## 4. 分数与Coverage

### 4.1 分数区间

```text
score_min = Σ(weight_d * rating_min_d / 4)
score_max = Σ(weight_d * rating_max_d / 4)
```

分数保留一位小数。它是有序筛选工具，不表示经济距离或概率。

### 4.2 Coverage

```text
presence_credit: supported=1, conflicted=1, unknown=0
resolved_credit: supported=1, conflicted=0, unknown=0
resolution_credit = 1 - (rating_max-rating_min)/4
decision_credit = resolved_credit * resolution_credit
```

```text
presence_coverage = Σ(weight*presence_credit)/Σ(weight)
resolved_coverage = Σ(weight*resolved_credit)/Σ(weight)
decision_coverage = Σ(weight*decision_credit)/Σ(weight)
```

强制不变量：

```text
0 <= decision_coverage <= resolved_coverage <= presence_coverage <= 1
```

同时输出`conflicted_weight_share`、`unknown_weight_share`和`stale_weight_share`。非加权硬门槛不进入Coverage分母，必须逐项判定。

### 4.3 Gate

```text
pass    = 全部必要谓词supported，且所有阈值字段rating_min >= threshold
fail    = 任一必要谓词有supported反证，或任一阈值字段rating_max < threshold
unknown = 不满足fail，且至少一个必要谓词或阈值仍未解析；包括conflicted
```

Gate必须保存`required_predicate_ids[]`、逐谓词状态和`decisive_claim_ids[]`。复合Gate先逐谓词判定，再按明确的AND/OR表达式聚合：AND中任一fail则fail、无fail但有unknown则unknown、其余pass；OR中任一pass则pass、无pass但有unknown则unknown、其余fail。禁止把unknown当fail或pass，也禁止对不同原子产品卡使用OR生成公司级产品状态。总分和Coverage不能补偿Hard Gate的`fail/unknown`。

## 5. Segment Chokepoint Score

### 5.1 权重

| 维度 | 权重 |
| --- | ---: |
| `demand_pressure` | 15 |
| `downstream_criticality` | 20 |
| `effective_supply_concentration` | 15 |
| `qualification_barrier` | 15 |
| `capacity_inelasticity` | 15 |
| `substitute_weakness` | 20 |
| **合计** | **100** |

`constraint_persistence`不计分，改为第12节派生`relief_horizon`。

### 5.2 0–4锚点

同一维度存在多个可比指标时，按该维度业务方向采用更强约束档；只有部分指标已知时输出可证明的上下界，不得把未知指标补成中性值。嵌套的定性条件按4→0从高到低求值。

| 维度 | 0 | 1 | 2 | 3 | 4 |
| --- | --- | --- | --- | --- | --- |
| Demand Pressure | 净需求下降、无增长或明确未传导 | 同口径净增长`>0且<5%` | `>=5%且<10%`，有直接传导 | `>=10%且<20%`，客户/订单/规格交叉支持 | `>=20%`，订单、产量或产品规格直接证明期限内传导，且库存口径已处理 |
| Downstream Criticality | 明确不影响交付、性能、成本、投产或合规 | 延迟`>=0且<1月`且量化影响`>=0且<5%`，并至少存在一项正向后果或轻微性能/成本影响 | 延迟`>=1且<=3月`或影响`>=5%且<=10%` | 延迟`>3月`、影响`>10%`或显著性能降级 | 直接阻止出货、投产、合规或关键性能；可由客户/工程/合规原文证明，不要求事故已经发生 |
| Effective Supply Concentration | 见5.3组件0档 | 见5.3组件1档 | 见5.3组件2档 | 见5.3组件3档 | 见5.3组件4档 |
| Qualification Barrier | `<=3月` | `>3且<=6月` | `>6且<=12月` | `>12且<18月` | `>=18月`，并有步骤、历史周期或当前里程碑原文 |
| Capacity Inelasticity | 认证完成后物理缺口`<=3月`关闭 | `>3且<=6月` | `>6且<=12月` | `>12且<18月` | `>=18月`且至少两类建设、设备、人员、能源、原料或制造良率约束 |
| Substitute Weakness | 生产使用中的替代路线可覆盖`>=30%`目标需求 | ready/production路线覆盖`>=10%且<30%` | 至少一条路线达到`credible_challenge`但ready覆盖未达10%，或ready路线覆盖`>0且<10%` | 路线最高只到原型、样品、测试或未兑现扩产，尚未达到`credible_challenge` | 完成反证搜索，并有明确失败、不合格、取消或产能不足证据证明期限内无路线满足 |

Criticality只评未缓解的原始后果；替代和绕行只进入Substitute Weakness。

Demand Pressure必须有同Scope、同期间、同定义的增长分母。只有绝对订单额、backlog或“需求强劲”而没有可比增长分母时，Demand Pressure保持`unknown [0,4]`；该原文可进入非加权诊断`positive_demand_presence=true`并支持Discovery，不得自动映射为1档，更不得证明收入、利润或卡点。

### 5.3 Effective Supply Concentration

```text
effective_output_i
= nameplate_i
* yield_i
* qualification_fraction_i
* target_scope_allocation_i
* availability_i

effective_share_i = effective_output_i / Σ(effective_output)
N_eff = 1 / Σ(effective_share_i^2)
qualified_failover_ratio
= largest_supplier_loss后其他合格可用余量 / target_demand
```

| 分数 | N_eff | 最大有效份额 | qualified failover ratio |
| ---: | --- | --- | --- |
| 0 | `>=4` | `<30%` | `>=30%` |
| 1 | `>=3且<4` | `>=30%且<40%` | `>=20%且<30%` |
| 2 | `>=2且<3` | `>=40%且<60%` | `>=10%且<20%` |
| 3 | `>=1.25且<2` | `>=60%且<80%` | `>0且<10%` |
| 4 | `<1.25` | `>=80%` | `0` |

三个组件分别映射。组件档位不一致时输出其评分包络，不取平均；组件缺失形成区间。只有同一客户/产品/地区/期间的like-for-like合格供应商进入本维度，不同技术路线进入Substitute Weakness。

### 5.4 Substitute聚合

每条替代路线`r`保存独立Scope并计算：

```text
route_ready_date(r)
= max(physical_capacity_ready, qualification_ready, ecosystem_ready)

qualified_alternative_output(r,t)
= nameplate * yield * qualification_fraction
* target_scope_allocation * availability

alternative_coverage_ratio(t)
= min(1, Σ mutually_exclusive qualified_alternative_output / unmet_target_demand)
```

共享产线、同一产出池和上下游重复路线必须去重。Segment Substitute评分由所有同范围路线的最强可实现聚合覆盖决定，不复制任何一家公司的Replacement总分。

### 5.5 Segment状态

`watch_segment`最低正向证据采用第10节Discovery Gate。

`segment_state_eligible=true`要求Scope与时效有效、无`evidence_mapping_error`，并满足以下至少一项：Discovery Gate通过、`segment_hard_fail=true`、或`resolved_upper_bounds_exclude_watch=true`。否则状态withheld。`resolved_upper_bounds_exclude_watch`要求Decision Coverage`>=0.65`，且`score_max<60`或受支持上界已经使Discovery Gate中的必要正向下限不可能满足。全unknown对象因此不具备状态资格。

| Gate | 条件 |
| --- | --- |
| `candidate_gate` | `score_min>=65`；Decision Coverage`>=0.65`；Demand和Criticality均`rating_min>=2`；需求侧和供应侧直接证据存在；反证搜索完成；强制维度无冲突 |
| `strong_candidate_gate` | Candidate通过；`score_min>=80`；Decision Coverage`>=0.80`；Demand和Criticality均`rating_min>=3`；至少两类独立供应约束；关键来源质量High |
| `watch_gate` | Discovery Gate通过；Presence Coverage`>=0.50`；Decision Coverage`>=0.20`；`score_max>=60` |
| `segment_hard_fail` | Demand或Criticality为受支持的`rating_max<1`，或Decision Coverage`>=0.60`且`score_max<40` |

唯一`primary_state`按以下优先级求值：`not_supported`（Hard Fail）→ `strong_candidate_chokepoint` → `candidate_chokepoint` → `watch_segment` → `not_supported`（resolved upper bounds exclude Watch）。若Eligible却无分支命中，输出`state_contract_error`。高档状态的`achieved_gates[]`累计保留已通过的低档Gate。

## 6. Company Defensibility Score

### 6.1 权重

| 维度 | 权重 |
| --- | ---: |
| `technical_performance_gap` | 20 |
| `qualification_lock_in` | 15 |
| `switching_cost` | 15 |
| `qualified_effective_capacity` | 20 |
| `quality_delivery_reliability` | 15 |
| `customer_sourcing_evidence` | 15 |
| **合计** | **100** |

`ecosystem_installed_base`改为Switching Cost的诊断输入，不独立加权。

### 6.2 0–4锚点

| 维度 | 0 | 1 | 2 | 3 | 4 |
| --- | --- | --- | --- | --- | --- |
| Technical Performance Gap | 合格替代者在目标要求上达到或超过 | 差异小且不影响目标采用 | 有可测优势，但替代者满足最低要求 | 优势对具名目标产品重要，替代者明显落后 | 多批次/多平台证明优势是必需条件，完成反证搜索后无合格同等路线 |
| Qualification Lock-in | 切换批准`<=3月` | `>3且<=6月` | `>6且<=12月` | `>12且<18月` | `>=18月`或需要重大重新设计/监管安全批准 |
| Switching Cost | 排除正式认证时钟后，迁移成本`<1%`且停机`<1月` | 成本`>=1%且<5%`或停机`>=1且<3月` | 成本`>=5%且<10%`或停机`>=3且<6月` | 成本`>=10%且<=20%`或停机`>=6且<=12月` | 成本`>20%`、停机`>12月`或重大保修/合规/报废风险 |
| Qualified Effective Capacity | 见6.3组件0档 | 见6.3组件1档 | 见6.3组件2档 | 见6.3组件3档 | 见6.3组件4档 |
| Quality/Delivery Reliability | 明确落后并有退货、延迟或客户流失 | 低于同范围合格同行 | 与同行相当 | 一个完整期间显著领先 | 至少两个期间/批次在良率、缺陷和交付上持续显著领先 |
| Customer Sourcing Evidence | 客户已成功切换、广泛多供或主动退出 | 客户明确降低依赖，替代已进入采购 | 双供/多供且公司仍为重要来源 | 公司是主供、二供规模有限或绑定较强 | 客户侧证明单供/长期绑定，且期限内无合格采购替代 |

Switching Cost中的成本与停机分别分档，维度取两者较高档；例如成本7%（2档）且停机8月（3档）时取3档。只有一个指标已知时，已知档`k>=1`形成`[k,4]`下限，另一个未知使更高档仍保持开放；已知指标只有0档时不能收窄整体区间，仍为`unknown [0,4]`；任一已知指标达到4档即可确定维度4档。

### 6.3 Qualified Effective Capacity

先对在位公司的合格有效份额和其他合格供应商的可用余量分别分档：

| 分数 | 公司有效份额 | 合格备用产出 / 目标需求 |
| ---: | --- | --- |
| 0 | `<20%` | `>=30%` |
| 1 | `>=20%且<30%` | `>=20%且<30%` |
| 2 | `>=30%且<50%` | `>=10%且<20%` |
| 3 | `>=50%且<70%` | `>0且<10%` |
| 4 | `>=70%` | `0` |

两个组件都已解析时，维度评分取`min(有效份额档, 备用稀缺档)`：高份额但存在充足合格备用不能形成高防御分。只有一个组件已知时，已知档只形成上限`[0,k]`；两个组件均未知时为`unknown [0,4]`。备用产出小于0或份额不在`[0,100%]`时输出`input_contract_error`。

### 6.4 状态

`defensibility_state_eligible`要求Decision Coverage`>=0.65`，Scope和时效有效，且Customer Sourcing为supported、非conflicted。

| Gate | 条件 |
| --- | --- |
| `high_defensibility` | `score_min>=70`；Decision Coverage`>=0.70`；Customer Sourcing`rating_min>=2`；Technical/Qualification/Switching/Capacity至少一项`rating_min>=3` |
| `low_defensibility` | `score_max<50`；Decision Coverage`>=0.65`；Customer Sourcing`rating_max<=2`；无Scope/时效缺口可实质改变结论 |
| `medium_defensibility` | Eligible且不满足High/Low |

唯一`primary_state`优先级为High → Low → Medium。High与Low同时通过为`state_contract_error`。

## 7. Replacement Momentum Score

### 7.1 权重与非加权Gate

| 加权维度 | 权重 |
| --- | ---: |
| `performance_parity` | 20 |
| `qualification_progress` | 15 |
| `capacity_readiness` | 15 |
| `customer_adoption` | 15 |
| `cost_tco_advantage` | 15 |
| `execution_delivery` | 10 |
| `regulatory_tailwind` | 5 |
| `architecture_tailwind` | 5 |
| **合计** | **100** |

`ecosystem_compatibility`权重为0，作为`replacement_ready`硬门槛。完整TCO包含认证、接口、工具、流程、停机和切换经济成本；Ecosystem只判断迁移是否技术/流程可行。

### 7.2 0–4锚点

同一维度若同时满足嵌套里程碑，取最高已证明档；例如全部批准必然包含某一批准、连续两周期覆盖也包含单周期覆盖，但分别取4档而不是重复或冲突计分。

| 维度 | 0 | 1 | 2 | 3 | 4 |
| --- | --- | --- | --- | --- | --- |
| Performance Parity | 明确不满足预定义关键要求 | 仅实验室/原型满足部分要求或测试不可比 | 可比测试满足全部最低关键要求 | 具名目标产品完成生产验证并满足全部要求 | 至少两个批次/期间或多个目标平台重复达到或超过 |
| Qualification Progress | 认证失败、取消、撤销或不合格 | 样品被接收测试，正式验证未开始 | 正式验证/design-in进行中，生产批准未完成 | 一个明确客户/产品完成认证和生产批准 | Scope内全部必要批准当前有效 |
| Capacity Readiness | 明确无可用产出、产线不可用或项目取消 | 资源落实但合格可售产出为零 | 有非零试产/爬坡产出，不能覆盖启动量 | 稳定覆盖已签启动量或`>=10%且<30%`需求一个周期 | 连续两个周期覆盖全部已签需求并有余量，或覆盖`>=30%`需求 |
| Customer Adoption | 客户拒绝、取消、移除或停止生产使用 | 付费评估、试点或初始商业订单 | 一个具名目标客户/平台已进入生产使用 | 两个具名生产客户，或一个具名生产客户跨两个可比周期持续使用 | 多个具名生产平台规模化采用，且同时有规模与连续性原文 |
| Cost/TCO Advantage | 完整可比TCO高出`>10%` | 高出`>5%且<=10%` | 差异在`±5%` | 降低`>5%且<10%`，经客户或量产周期验证 | 降低`>=10%`，跨两个周期或客户验证 |
| Execution/Delivery | 明确资源不足、关键承诺失败或项目取消 | 只有公告、路线图或非约束意向 | 约束性资金/设备/团队/供应落实，但里程碑风险高 | 当前项目按计划，且有一个相似成功量产记录 | 当前扩产连续兑现，至少两个相似项目成功且承诺有效 |
| Regulatory Tailwind | 生效政策/限制阻止采用 | 显著增加时间、成本或可服务范围限制 | 官方证据证明中性 | 生效政策直接降低认证/采购壁垒 | 强制标准、采购或资金机制直接推动期限内规模采用 |
| Architecture Tailwind | 目标架构排斥该路线 | 需要重大架构修改 | 架构兼容但无偏好 | 架构变化降低集成或多供门槛 | 架构明确要求或强烈推动该路线 |
| Ecosystem Compatibility（非加权） | 无法兼容或必须更换核心架构 | 重大工具链/流程/架构重建 | 需要明显适配和重新认证但可行 | 一个目标平台完成集成且迁移受控 | 多平台通过标准接口/工具/流程低摩擦采用 |

TCO、Regulatory和Architecture没有原文时为unknown，不得默认2分。制造良率只进入Capacity；收入和份额不进入Customer Adoption高档。

### 7.3 多轴里程碑

```yaml
product_readiness: concept | prototype | sample_ready
lifecycle: active | failed_or_withdrawn
qualification: not_started | testing | design_selected | qualified | production_approved
production: none | pilot | ramp | mass_production | stable_scaled_output
adoption: evaluation | paid_pilot | production_use | recurring_use | multi_platform_use
financial: no_revenue | realized_unquantified | single_period_material | material_revenue | recurring_material_revenue
share_trajectory: losing | stable | single_period_gain | sustained_gain
displacement: none | partial | confirmed
```

每条轴独立保存证据区间。`sustained_gain`至少需要三个可比时点和两个连续提升区间。

Share与Displacement同时保存0–4有序等级，不能用一个轴替代另一个：

| 轴 | 0 | 1 | 2 | 3 | 4 |
| --- | --- | --- | --- | --- | --- |
| Share Trajectory | 同口径份额下降 | 至少两个观测点基本稳定 | 一个正向变化区间，即至少两个观测点 | 连续两个正向区间，即至少三个观测点 | 连续至少三个正向区间，并保持同产品、地区、分母和定义 |
| Displacement | 明确未采用、撤回或移除挑战者 | 测试中，未影响原供应商采购 | 新增第二来源/新增采用，但未证明原供应商份额下降 | 同一客户和产品有部分采购量从原供应商迁移 | 同一客户和产品的替换持续至少两个期间，并有采购量或份额迁移原文 |

### 7.4 Hard Gates与状态

`replacement_state_eligible=true`要求Scope与时效有效、无`evidence_mapping_error`，并满足以下之一：通过第10.1节Replacement Discovery Gate；或存在受支持的`failed_or_withdrawn_gate`。否则`primary_state=null`并给出`withheld_reason`。

| Gate | 规则 |
| --- | --- |
| Failed/Withdrawn | 同一原子产品代际有当前有效原文明示取消商业化、撤回上市、停止生产使用、认证失败且终止，或将产品改为不对外销售的内部测试用途 |
| Performance | supported且`rating_min>=2` |
| Qualification | supported且`rating_min>=3` |
| Capacity | supported且`rating_min>=2` |
| Ecosystem | supported且非加权诊断`rating_min>=2` |
| Fatal Blocker | 完成规定反证搜索；无受支持的致命TCO、监管或架构阻断 |

| 状态 | 必要条件 |
| --- | --- |
| `failed_or_withdrawn` | Failed/Withdrawn Gate=pass；记录失败类型、生效日期和决定性原文；不得同时命中任何正向Replacement状态 |
| `early_signal` | Eligible，但尚未通过`credible_challenge`；保留已通过的产品级正向Gate与明确缺口 |
| `credible_challenge` | Scope完整；`score_min>=40`；Decision Coverage`>=0.35`；Qualification或Adoption至少一个正向下限；Performance不为fail；无明确disqualifier |
| `replacement_ready` | `score_min>=60`；Decision Coverage`>=0.60`；五个Hard Gate全部pass |
| `production_alternative` | Ready；目标客户/平台实际生产使用；同范围合格可售产出存在 |
| `scaled_alternative` | Production；Adoption和Capacity均`rating_min>=3`；至少两个交付周期；并满足市场存在`>=5%`、目标需求覆盖`>=10%`或至少两个目标平台之一 |
| `realized_replacement` | Production；Performance、Qualification、Capacity、Adoption、Ecosystem均supported且`rating_min>=3`；Fatal Blocker=pass；Displacement supported且`rating_min>=3` |
| `scaled_replacement` | Realized；Displacement`rating_min>=4`；且通过`scaled_alternative`行定义的Scale Gate |

实际displacement只接受：同范围直接替换/移除/采购迁移/二供转主供，或至少三个可比时点的严格份额三角验证并排除纯市场扩张。客户可匿名，但Scope必须稳定可识别。仅有“actual displacement evidence存在”而其他Realized硬门低于3或unknown时，仍不能输出`realized_replacement`。

输出累计`achieved_gates[]`和唯一`primary_state`，优先级：Failed/Withdrawn → Scaled Replacement → Realized → Scaled Alternative → Production → Ready → Credible → Early Signal。`failed_or_withdrawn`是负向终态，不得解释为挑战者动量；如果同一张卡同时出现当前有效失败与正向状态，输出`atomic_product_scope_error`，不得用正向总分覆盖失败。产品后来以新代际或重新立项恢复时必须创建新`product_id`卡，并保留lineage。`production_alternative`继承Ready；Realized还执行更高的六项不可补偿门，因此Performance、Qualification、Capacity、Adoption、Ecosystem或Displacement任一未达到3时，总分和收入均不能补偿。

## 8. Earnings Transmission Score

### 8.1 财务口径

```text
issuer_materiality = 产品外部确认收入 / 公司同期间外部收入
segment_materiality = 产品外部确认收入 / 稳定可比报告分部同期间外部收入
market_presence = 同产品收入或出货 / 同地区同期间目标市场
```

`market_presence`不得证明对公司或分部利润重要。订单、backlog、内部转移、sell-in和未来管理层目标不得冒充已确认外部收入。

财务状态：

| 状态 | 规则 |
| --- | --- |
| `no_revenue` | 明确无收入、退出或取消 |
| `realized_unquantified` | 已确认外部收入，但分母未知 |
| `single_period_material` | 一个同口径期间达到公司`>=1%`或分部`>=5%` |
| `material_revenue` | 连续两个同口径季度或TTM达到公司`>=1%`或分部`>=5%` |
| `recurring_material_revenue` | 连续四季度达到门槛，并排除一次性集中交付、库存或会计重分类 |

### 8.2 权重

| 维度 | 权重 |
| --- | ---: |
| `revenue_materiality` | 20 |
| `volume_realization_leverage` | 15 |
| `pricing_power` | 15 |
| `margin_transmission` | 20 |
| `time_to_revenue` | 10 |
| `capital_cash_burden` | 10 |
| `customer_concentration_risk` | 5 |
| `earnings_persistence` | 5 |
| **合计** | **100** |

### 8.3 0–4锚点

Revenue Materiality和其他连续里程碑按最高已证明档求值；达到连续四季度时取4档，不同时再生成2/3档计分Claim。

| 维度 | 0 | 1 | 2 | 3 | 4 |
| --- | --- | --- | --- | --- | --- |
| Revenue Materiality | 明确无收入/退出/取消 | 有已确认正收入，但所有适用且可比的公司/稳定分部分母均未达到公司`1%`或分部`5%` | 单一期间达到公司`1%`或分部`5%` | 连续两期或TTM达到公司`1%`或分部`5%` | 连续四季度达到门槛并排除一次性因素；若同时达到公司`5%`或分部`15%`可支持更强下限 |
| Volume Realization Leverage | 明确无合格产能承接需求或销量取消 | 有产出但规模/利用率不足，增量成本高 | 有部分可售产能，销量增长可兑现但受良率/分配限制 | 一个期间以较高利用率将需求转化为显著销量 | 至少两个期间持续兑现，单位成本或产能利用率形成明确经营杠杆 |
| Pricing Power | 明确降价、固定价格或成本无法转嫁 | 提价低于成本增幅或仅一次性mix | 价格/合同大体中性，或部分成本可转嫁 | 一个期间有同产品提价、稀缺分配或合同改善 | 至少两个期间/客户持续提价、改善mix或维持稀缺溢价 |
| Margin Transmission | 增量收入明确压低利润或被成本完全吸收 | 增量利润率低于公司/分部且爬坡拖累明显 | 已有正增量利润，但幅度有限或口径较宽 | 一个期间产品/稳定分部利润率显著改善并能归因 | 至少两个期间产品级或稳定分部数据证明高增量利润持续转化 |
| Time to Revenue | 预计确认时间超出研究期限或项目取消 | 首次主要收入位于研究期限最后四分之一 | 首次主要收入位于期限中段 | 首次主要收入位于期限前半段 | 主要收入已确认，且当前期间仍在持续 |
| Capital/Cash Burden | 资金缺口、CapEx/营运资金可能超过收益或项目取消 | 高投入且预计回收期明显超出研究期限 | 投入可融资，回报路径可见但现金/折旧压力明显 | 现有资产复用或回收期位于研究期限内 | 客户预付款/约束合同/轻资产结构使现金负担低且回报清晰 |
| Customer Concentration Risk | 单一客户可取消/压价/内制且无保护 | 高度集中，仅有非约束关系 | 集中但有部分合同、切换成本或扩客计划 | 多客户或约束合同显著限制取消/压价 | 客户分散且多期合同/切换结构共同保护收入 |
| Earnings Persistence | 明确一次性、库存释放或`<3月` | `>=3且<6月` | `>=6且<12月` | `>=12且<18月` | `>=18月`，有多期需求、合同、结构性单位用量或长期约束支持 |

“无分母”或仍有适用分母未知时不能形成Revenue Materiality精确1分，只能是`supported + lower_bound [1,4]`。1分的精确/上界结论必须覆盖所有适用口径；不存在稳定可比报告分部时只要求公司口径。

### 8.4 Earnings状态

`earnings_state_eligible`要求Decision Coverage`>=0.65`，财务Scope/期间/分母可比，且Revenue Materiality、Time to Revenue、Margin Transmission均非unknown/conflicted。

| Gate | 条件 |
| --- | --- |
| `earnings_hard_fail` | 三个强制维度任一受支持的`rating_max<2`，或资本/现金负担有足以否定期限内利润捕获的明确失败 |
| `material_earnings_gate` | Hard Fail=false；`score_min>=65`；Decision Coverage`>=0.70`；三个强制维度均`rating_min>=2` |
| `weak_earnings_gate` | Hard Fail=true，或Decision Coverage`>=0.65`且`score_max<50` |
| `moderate_earnings_path` | Eligible且不满足Material/Weak |

唯一`primary_state`使用`weak_earnings_capture`、`material_earnings_path`、`moderate_earnings_path`，优先级：Hard Fail/Weak → Material → Moderate。关键字段unknown时withheld，不能输出Moderate。

## 9. Company Competition State

Competition只评产业竞争位置，Earnings作为单独标签展示。

进入状态前必须满足：

```yaml
competition_state_eligible: true
incumbent_defensibility_primary_state: high | medium | low
challenger_set:
  challenger_set_id:
  assessment_scope:
  search_protocol_version:
  included_candidates: []
  excluded_candidates_with_reason: []
  search_completed_at:
  as_of_date:
  coverage_gate: pass
```

“所有挑战者”只指该冻结集合，不表示开放世界完备。

| 优先级 | primary_state | 规则 |
| ---: | --- | --- |
| 1 | `vulnerable_incumbent` | 任一挑战者有受支持`realized_replacement`；或Defensibility=`low_defensibility`且存在`replacement_ready`或更高挑战者 |
| 2 | `contested_chokepoint_owner` | Defensibility为`high_defensibility/medium_defensibility`，存在`credible_challenge`或更高但尚无Realized |
| 3 | `durable_chokepoint_owner` | Defensibility=`high_defensibility`；冻结集合内无`credible_challenge`；客户依赖证据当前有效 |
| 4 | `differentiated_incumbent` | Defensibility=`medium_defensibility`；冻结集合内无`credible_challenge` |
| 5 | `commodity_or_weakly_differentiated` | Defensibility=`low_defensibility`，且没有ready或realized挑战者 |

Low Defensibility、只有尚未ready的Credible挑战者时，主状态仍为Commodity，`achieved_gates[]`保留挑战信号。任何实际displacement优先于历史防御分。

挑战者标签从Replacement累计Gate派生：

```text
emerging_replacement  <- credible_challenge
qualified_replacement <- replacement_ready
production_supplier   <- production_alternative
scaled_supplier       <- scaled_alternative
realized_replacement  <- realized_replacement
```

`failed_or_withdrawn`不生成正向挑战者标签，只作为负向生命周期结果展示；它不能被计入“存在credible挑战者”的集合条件。

## 10. Candidate Selection与10卡预算

### 10.1 Discovery Gate

Segment至少满足：

```text
Scope完整
AND Demand rating_min>=1
AND Criticality rating_min>=1
AND Concentration/Qualification/Capacity/Substitute至少一项rating_min>=2
AND 一条直接需求/客户侧Primary Claim
AND 一条直接供应侧Primary Claim
```

Replacement至少满足：

```text
Scope完整
AND Performance/Qualification/Production/Capacity/Adoption至少一个产品级正向Primary Claim
AND Performance Gate != fail
```

对于Demand，`positive_demand_presence=true`可以满足Discovery中的“存在直接需求信号”，但不能提高Demand Pressure评分，也不能单独进入`confirmed_candidate`。订单/backlog是否转化为收入与卡点必须在Earnings和Segment各自Gate中独立验证。

纯政策、融资、CapEx、设备订单、市场预测或路线图不能单独通过。反例卡必须有明确负向、false-leader或卡点迁移事实。全unknown对象永不通过。

### 10.2 Review与Confirmed

| 状态 | 条件 |
| --- | --- |
| `review_candidate` | Discovery Gate通过；Presence Coverage`>=0.50`；Decision Coverage`>=0.20`；至少一个正向下限；列出`missing_material_questions` |
| `confirmed_candidate` | 对应业务状态Gate通过；Decision Coverage`>=0.65`；所有强制Gate=pass；强制维度无conflicted/stale；来源与反证搜索要求通过 |

各状态更高的Decision Coverage要求优先适用，例如Strong Segment`>=0.80`。

### 10.3 十卡分配

每个Theme最多10张`company+product+segment+customer/region+horizon+as_of`卡：

| lane | 数量 | 排序键（从高到低，字典序） |
| --- | ---: | --- |
| `proven_floor` | 6 | 已通过强制Gate数量；`score_min`；Decision Coverage；High来源占比；`as_of_date` |
| `bounded_upside` | 2 | `score_max`；`score_min`；可解决的强制Gate数量；直接Primary Claim数；`as_of_date` |
| `counterexample_or_transfer` | 2 | 实际displacement/明确失败优先；迁移影响范围；直接证据质量；`as_of_date` |

同一Segment原则上最多5张，每个重要Segment至少1张。某lane不足时，按`counterexample → bounded_upside → proven_floor`的顺序把空位回流给其他合格lane，但不得突破Segment上限。

最终tie-breaker固定为：冲突权重更低 → unknown权重更低 → `company_id/product_id/segment_id`升序。禁止模型自由打破平局。

严格公司排序只有在`A.score_min > B.score_max`时成立；否则输出`overlapping`。卡片lane内顺序是人工复核优先级，不是投资排名。

## 11. Materiality与Share Trajectory

`market_presence>=5%`可满足Replacement Scale Gate的一个条件，但不得满足公司/分部Material Revenue。

`sustained_gain`要求：

```text
至少三个可比时点
+ 两个连续份额提升区间
+ 同产品、地区、客户范围、期间、分母和份额定义
```

两个时点只能支持`single_period_gain`。市场扩大导致收入增长但份额稳定，不是Share Gain；份额增长也不自动证明同客户displacement。

## 12. Relief Horizon与Constraint Transition

### 12.1 时间与路线

默认时间桶为月；若关键补货/生产周期短于月，使用周。所有库存、产出、订单和预测必须转换到同一时间桶和单位。

```text
route_ready_date(r)
= max(
    physical_capacity_ready_date,
    qualification_ready_date,
    ecosystem_integration_ready_date
  )

qualified_output(r,t)
= nameplate(r,t)
* yield(r,t)
* qualification_fraction(r,t)
* target_scope_allocation(r,t)
* availability(r,t)
```

### 12.2 库存—流量

```text
available_before_service(t)
= inventory_begin(t)
+ Σ mutually_exclusive qualified_output(r,t)
- scrap_or_unavailable(t)

gross_requirement(t)
= demand(t) + backlog_begin(t)

served_demand(t)
= min(available_before_service(t), gross_requirement(t))

backlog_end(t)
= gross_requirement(t) - served_demand(t)

inventory_end(t)
= available_before_service(t) - served_demand(t)
```

库存必须扣除已分配、安全锁定、过期、规格不符和物流不可用数量；同一库存只能释放一次。Primary与Alternative共享产线或产出池时必须互斥去重。

### 12.3 需求

```text
若forecast是总需求：
  demand(t)=max(firm_orders_due(t), calibrated_total_forecast(t))

若forecast明确只含未进入订单的残余需求：
  demand(t)=firm_orders_due(t)+residual_forecast(t)
```

必须保存`forecast_includes_orders`。历史期间用实际消耗/交付回测预测偏差。

### 12.4 Relief条件

节点缓解必须同时满足：

```text
backlog_end == 0
fill_rate >= required_fill_rate
inventory_end >= safety_stock_target
连续满足 >= max(一个季度, 一个完整补货周期)
```

输出Base和Stress两个情景：

```yaml
relief_horizon_base:
relief_horizon_stress:
node_relief_date:
system_relief_date:
relief_confidence:
constraint_transition_state:
```

相邻节点继续阻止最终交付时，`node_relief=true`但`system_relief=false`。原节点与新节点同时阻塞为`coexisting_constraints`；原节点缓解且约束移动到相邻节点才是`transferred`。季节性低谷中的短暂平衡不构成Relief。

## 13. Monitoring与趋势状态

### 13.1 时间字段

每个新事实保存：

```yaml
event_time:       # 业务事实实际生效时间
published_at:     # 来源发布时间
retrieved_at:     # 系统获取时间
assessment_as_of: # 被更新快照的截止时间
```

### 13.2 变化类型

| change_type | 定义 | 可触发业务趋势吗 |
| --- | --- | --- |
| `knowledge_revision` | 新找到、纠正或重新解释旧期间事实 | 否；只更新历史快照并输出`assessment_revised` |
| `industry_event` | 新期间真实需求、供应、认证、采用、收入或替代变化 | 是 |

### 13.3 趋势优先级

趋势只比较Scope、口径、权重和时间桶一致的快照：

```text
IF change_type == knowledge_revision
  trend_state = assessment_revised
ELSE IF supported evidence invalidates Demand/Criticality mandatory floor
  trend_state = invalidated
ELSE IF original node remains constrained AND adjacent node becomes constrained
  trend_state = coexisting_constraints
ELSE IF original node is relieved/weakening AND adjacent node becomes candidate+
  trend_state = transferred
ELSE IF score_min rises >=10 OR a mandatory business gate advances one level
  trend_state = strengthening
ELSE IF score_max falls >=10 OR a mandatory business gate regresses one level
  trend_state = weakening
ELSE
  trend_state = unchanged
```

`unknown`、`conflicted`或`stale`只能改变证据轴，不能单独触发`invalidated/weakening`。状态迁移必须引用导致变化的Claim和`event_time`。

## 14. 标准输出合同

```json
{
  "contract_id": "theme-chokepoint-scoring-v1.2",
  "assessment_id": "...",
  "assessment_scope": {
    "company_id": null,
    "product_id": "...",
    "segment_id": "...",
    "customer_or_platform_scope": "...",
    "geography": "Global",
    "time_horizon_months": 12,
    "as_of_date": "2026-08-15"
  },
  "dimensions": [
    {
      "name": "...",
      "weight": 15,
      "rating_min": 2,
      "rating_max": 4,
      "evidence_state": "supported",
      "bound_type": "lower_bound",
      "primary_claim_ids": ["..."],
      "floor_only_claim_ids": [],
      "context_claim_ids": [],
      "condition_coverage": {
        "required_condition_count": 0,
        "resolved_required_condition_count": 0,
        "condition_ids": []
      },
      "missing_material_questions": []
    }
  ],
  "earnings_overlay": {
    "weighted": false,
    "included_in_segment_score": false,
    "diagnostics": []
  },
  "score_min": 0.0,
  "score_max": 100.0,
  "coverage": {
    "presence": 0.0,
    "resolved": 0.0,
    "decision": 0.0,
    "conflicted_weight_share": 0.0,
    "unknown_weight_share": 0.0,
    "stale_weight_share": 0.0
  },
  "hard_gates": {},
  "business_state": {
    "eligible": false,
    "withheld_reason": "insufficient_evidence",
    "achieved_gates": [],
    "primary_state": null
  },
  "trend": {
    "change_type": null,
    "trend_state": null,
    "event_time": null,
    "prior_assessment_id": null
  },
  "candidate_lane": null,
  "missing_material_questions": [],
  "counterevidence_search": {
    "protocol_version": "...",
    "completed": false,
    "query_log_ids": [],
    "negative_findings": []
  }
}
```

必须保存未四舍五入的原始分数和全部Claim引用。实现不得只返回一个总分。

## 15. 明确禁止

- 用市场份额或“龙头”直接输出Durable Owner；
- 用收入、量产或份额增长替代Actual Displacement Gate；
- 用`score_max`单独产生Review Candidate；
- 用unknown生成0分、2分或“中性”；
- 用同一原子事实满足多个维度3/4分；
- 将制造良率同时计入Performance与Capacity；
- 将认证/design win同时计入Qualification与Adoption高档；
- 将完整TCO中的迁移成本再次作为Ecosystem加权分；
- 将绕行/替代能力同时计入Criticality与Substitute；
- 将HBM stack、HBM assembly/test与2.5D integration混成一个Segment；
- 将补录旧证据误报为产业Strengthening；
- 在未冻结challenger_set时输出Durable Owner；
- 在强制Gate conflicted/unknown时输出Confirmed状态；
- 用模型置信度、来源数量或搜索摘要收窄业务区间。
- 把绝对订单额或backlog在没有同Scope增长分母时自动映射到Demand Pressure 1–4档；
- 把`being deployed`、未来GW承诺、未来ramp或管理层目标当作当前合格产出、生产采用或已确认收入；
- 把当前产品与继任产品、内部测试芯片与外售产品放在同一张卡并用OR Gate聚合；
- 在评分payload中使用未声明别名`capital_intensity`；
- 将`earnings_overlay`诊断放入Segment 100分或Coverage分母；
- 将明确取消商业化或撤回的产品输出为`early_signal`。

## 16. Golden回归门槛

冻结前必须通过：

1. HBM与2.5D替代路线分Segment；
2. Samsung HBM4量产销售不自动证明Material Revenue、Performance Gate或Displacement；
3. Micron HBM4单季收入重要性不自动证明持续份额或Displacement；
4. SK hynix量产准备不自动证明Mass Production；
5. Amkor HDFO既有量产不迁移到CoWoS替代关系；
6. Amkor歧义transcript不提高Qualification下限；
7. Performance=0时任何Replacement总分都不能进入Ready/Realized；
8. 全unknown Segment不能进入Watch；
9. 客户采购unknown时Defensibility状态withheld；
10. Margin Transmission unknown时Earnings状态withheld；
11. High Defensibility但存在Realized Replacement时Competition=`vulnerable_incumbent`；
12. Knowledge Revision不能触发Strengthening。
13. 一个复合锚点缺少任一required谓词时，不能形成该档下限；Condition Coverage不能补偿；
14. 同一`origin_event_id/evidence_family_id`的转载、段落或页面不能满足独立多来源/多事件条件；
15. 即使Displacement有受支持3分下限，Performance、Qualification、Capacity、Adoption或Ecosystem任一低于3或unknown时也不能进入Realized。
16. 明确取消商业化、改为内部测试或撤回上市的原子产品输出`failed_or_withdrawn`，不能输出`early_signal`；
17. `being deployed`与未来GW承诺混合且不能拆分时，不能证明当前Production、Capacity 3档或Adoption 3档；
18. 绝对订单或backlog没有同Scope增长分母时，Demand Pressure保持unknown，但`positive_demand_presence`可用于Discovery；
19. `capital_intensity`进入评分payload必须被拒绝，唯一正式字段为`capital_cash_burden`；
20. `earnings_overlay`不得改变Segment score与Coverage；
21. Hard Gate的AND/OR三态真值表必须保持unknown，不得把unknown折算成pass/fail；
22. 当前产品与继任产品处于同一assessment时必须返回`atomic_product_scope_error`。

HBM回归结果必须记录输入文件哈希、断言结果和证明边界。全部22条断言通过只代表边界反例回归通过；冻结还必须完成第17节跨主题正例与标注一致性Gate。

## 17. 版本变更

### 17.1 重新冻结Gate

v1.2从`freeze_candidate`转为`frozen`前必须同时满足：

1. HBM/先进封装及v1.2新增边界共22条断言全部通过；
2. 完成AI数据中心电力/冷却跨主题Golden；若两个主题仍缺Durable、Realized或Weak Earnings真实正例，必须增加补充真实案例，不得伪造标签；
3. 至少两名标注员在不知道对方结果和预期答案的情况下，独立完成全新v0.4冻结样本；不得复用任何已解盲、已裁决或已进入旧Golden人工审核的公司/产品案例；
4. 保存逐维度、Hard Gate和最终状态的分歧、裁决理由及混淆矩阵；
5. 对0–4线性权重只主张候选排序启发式，并用成对排序和Top-10人工复核结果验证其没有明显系统性反序；
6. 产品负责人批准标注一致性、状态错误率和候选召回的数值验收阈值；
7. v0.4预承诺至少10对真实`exact/exact`，样本设计至少包含16个`exact_capable=true`条目作为余量；每个exact-capable条目必须在固定原文中同时具备档位下限与排除更高档的上界；
8. v0.4必须同时包含Hard Gate明确pass、fail、unknown，至少一个`failed_or_withdrawn`正例、一个current/future混合反例及一个“订单不等于收入/卡点”反例；
9. 两个标注ZIP字节完全相同，且盲包污染审计证明不包含selection rationale、预期答案、裁决、agreement工具、历史结果或可反推出标签的文件名/metadata；
10. 一致性门只使用两份不可变原始submission计算；裁决结果不得回写或冒充预裁决agreement。只有预裁决门通过后，才运行HBM回归与Top-10人工复核。

第6项数值阈值已于2026-08-16批准并记录在v1.2迁移计划：Hard Gate一致率`>=0.95`、Final State一致率`>=0.90`、至少10对真实exact/exact、线性加权kappa`>=0.70`、Durable/Realized单边高风险升级为0。不得仅凭单主题边界回归恢复`frozen`。

### 17.2 变更升级

以下变化必须升级合同版本并重跑Golden：

- 维度、权重、0–4锚点；
- Gate阈值、Coverage公式或状态优先级；
- assessment_scope或时间语义；
- Candidate容量、lane或排序规则；
- Materiality、Share、Displacement或Relief定义；
- Claim复用、冲突或来源资格规则。

新增来源和Claim但不改变语义时，只更新Golden/数据版本，不改合同版本。
