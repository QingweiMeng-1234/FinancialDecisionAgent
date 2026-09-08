# Theme Chokepoint Scoring Contract v1.5

## 1. 状态与继承

| 字段 | 值 |
| --- | --- |
| contract_id | `theme-chokepoint-scoring-v1.5` |
| 状态 | `freeze_candidate`（2026-08-20） |
| 基础合同 | v1.4未被本文件明确替换的Bound Basis、Coverage、Hard Gate与状态优先级 |
| 本版语义变化 | Segment六维中的Demand Pressure、Downstream Criticality、Effective Supply Concentration、Capacity Inelasticity、Substitute Weakness，以及Stage 3/4证据与完成边界 |
| 机器合同 | `tools/theme-chokepoint/semantic-task-contract-v1.5.json` |
| Runtime Prompt | 独立`segment-fact-extractor-v1.5.md`；模型抽事实，代码机械评分 |
| 验证状态 | Golden、Holdout、HBM边界回归与Top-10复核均未重跑 |

本文件不修改、覆盖或重新解释已冻结的`theme-chokepoint-scoring-v1.4`。v1.4的文档、机器合同、盲标包、提交回执、裁决、Golden和Holdout证据保持原SHA与原语义。

Qualification Barrier、Company三族评分、Replacement、Earnings以及未被本文件替换的Bound Basis、Evidence Capability、Coverage、Hard Gate和状态优先级继续继承v1.4。其余明确列出的Segment与Stage边界以本合同为准。

v1.5机器合同、Prompt、validator和边界测试已经建立，但Golden、Holdout、HBM边界回归与Top-10人工复核尚未完成。因此当前只能是`freeze_candidate`，不得标记为`frozen`或声称已通过生产验收。v1.4默认运行路径保持不变；只有显式启用v1.5资产与运行模式才执行本合同。

## 2. Demand Pressure年度增长合同

### 2.1 评分对象

`demand_pressure`只评估：在当前`AssessmentScope`内，需求是否以可比的年度增长率增长，并直接传导到当前Segment。

它不评估市场热度、TAM、绝对订单额、绝对backlog、公司收入、利润、供应紧张程度或下游缺货后果。

### 2.2 Canonical Annual Growth Rate

记`g`为同Scope、同定义、同单位、同长度可比期间的年增长率。允许的`annual_growth_basis`仅包括：

| annual_growth_basis | 定义 |
| --- | --- |
| `monthly_yoy` | 同一自然月相对上年同月的增长率 |
| `quarterly_yoy` | 同一自然季度相对上年同季度的增长率 |
| `ttm_yoy` | 连续12个月相对前一组连续12个月的增长率 |
| `cagr_annualized` | 跨多年的复合年增长率 |
| `seasonally_adjusted_annualized` | 已季调月度/季度数据按明确公式年化后的增长率 |

原始MoM或QoQ不得直接套用本合同的0–4档。若使用季调年化口径，必须持久化季调状态、年化公式和原始期间；否则该证据只能作为`context_only`，不得改变Demand Pressure评分边界。

计算规则：

```text
yoy_growth = comparison_value / base_value - 1

cagr_annualized
= (comparison_value / base_value) ^ (12 / elapsed_months) - 1
```

`base_value<=0`、期间长度不一致、单位不一致、Scope不一致、定义变化、分母无法复核或季调/年化方法不明时，`g`不可用于评分。

### 2.3 0–4锚点

边界采用无重叠定义。`g=10%`进入2档，`g=20%`进入3档，`g=30%`仍属于3档；只有`g>30%`才进入4档。

| 档位 | Canonical Anchor |
| ---: | --- |
| 0 | `g<=0%`，或有直接原文证明需求未传导到当前Segment |
| 1 | `0%<g<10%`，且需求已直接传导到当前Segment |
| 2 | `10%<=g<20%`，且需求已直接传导到当前Segment |
| 3 | `20%<=g<=30%`，且需求已直接传导到当前Segment |
| 4 | `g>30%`，且直接传导与库存口径均完成验证 |

因此：

```text
g=0%   -> 0档
g=10%  -> 2档
g=20%  -> 3档
g=30%  -> 3档
g=40%  -> 4档
g>40%  -> 4档（自然封顶，不创建5档）
```

“>30%=4分”表示30%本身仍为3档，超过30%后进入最高档；40%以上仍保持4档，而不是无定义或继续加分。

### 2.4 直接传导谓词

任何正向档位1–4都必须有至少一条当前Scope内的直接传导Primary Claim，证明被测需求确实到达当前Segment。只有宏观主题、邻近产品、TAM、行业预测、绝对订单额、绝对backlog或“需求强劲”等原文时，Demand Pressure保持`unknown [0,4]`；不得自动给1档。

直接传导证据至少要把以下对象连接起来：

```text
目标下游产品或工作负载
-> 单位产品对当前Segment的需求量、配置、订单、产量或可复核使用量
-> 当前AssessmentScope内的Segment需求
```

### 2.5 库存口径谓词

exact 4必须处理与当前Scope相关的渠道库存、客户库存、提前采购、去库存、重复下单与取消风险，证明`g>30%`不是库存时点或订单口径造成的假增长。

库存证据按以下规则影响Bound：

1. 若`g`已经来自可复核的净需求序列，直接传导已支持，但独立的库存谓词尚未关闭，则最多输出`lower_bound [3,4]`，不得输出exact 4。
2. 若`g`来自订单、出货或backlog，且库存变化可能改变净需求方向或档位，则不得形成3档floor；保持`unknown [0,4]`，直到库存影响可被约束。
3. 搜索未发现库存披露不等于“库存无影响”，不得形成ceiling或exact。

### 2.6 Bound Basis

Demand Pressure继续遵守v1.4的Bound Basis合同：

- `exact 4`要求`floor_anchor=4`、`ceiling_anchor=4`、`exact_basis=natural_cap`，且年增长、直接传导、库存三个谓词全部supported；
- `exact k`且`k<4`必须同时有受支持floor与排除全部更高档的直接ceiling；
- 只有增长下限时输出`lower_bound [k,4]`；
- 只有增长上限时输出`upper_bound [0,k]`；
- 增长、传导或库存事实相互冲突时输出`conflicted`，不得平均；
- 缺少可比增长分母或直接传导时输出`unknown [0,4]`，不得用搜索缺失生成0档。

### 2.7 必须持久化的最小字段

后续v1.5机器合同至少必须为每个可评分Demand事实保存：

```json
{
  "metric_name": "qualified_segment_demand_units",
  "unit": "units",
  "base_period_start": "2025-01-01",
  "base_period_end": "2025-12-31",
  "base_value": 100.0,
  "comparison_period_start": "2026-01-01",
  "comparison_period_end": "2026-12-31",
  "comparison_value": 115.0,
  "annual_growth_basis": "ttm_yoy",
  "annual_growth_rate": 0.15,
  "seasonally_adjusted": false,
  "annualization_formula": null,
  "direct_transmission_evidence_ids": ["evidence-demand-transmission"],
  "inventory_treatment": "verified_no_material_distortion",
  "inventory_evidence_ids": ["evidence-inventory"]
}
```

字段缺失时必须按第2.5和2.6节降级，不得由模型根据常识补齐。

## 3. 示例

### 3.1 可评分的15%年度增长

```text
2025 TTM同Scope净需求 = 100
2026 TTM同Scope净需求 = 115
g = 15%
直接传导 = supported
更高档增长已由同一完整期间排除
```

结果：

```text
Demand Pressure = exact 2
```

### 3.2 35%增长但库存谓词未关闭

```text
g = 35%
数据是可复核净需求序列
直接传导 = supported
库存谓词 = unknown
```

结果最多为：

```text
Demand Pressure = lower_bound [3,4]
```

### 3.3 35%订单增长但库存可能重复计算

```text
订单增长 = 35%
直接传导 = supported
渠道库存和重复下单影响未知
```

由于订单增长不等于净需求增长：

```text
Demand Pressure = unknown [0,4]
```

### 3.4 8%月环比

```text
MoM = 8%
没有季调和年化方法
```

结果：

```text
scoring_use = context_only
Demand Pressure不因该证据改变
```

## 4. Downstream Criticality

本维度采用“结构性硬阻断＋标准化运营冲击”双层模型。

结构测试永久移除当前Segment的100%能力，不计技术替代和重新设计。只有当前原子`产品×平台×地区`Scope的全部合格输出无法出货、投产、合规或达到关键性能，`structural_hard_block=pass`并得到exact 4。部分批次、单一供应商或临时停产均不满足结构测试。

运营测试假设最大有效供应商100%停供90天，允许现有库存和90天内同路线、已认证Failover，排除新技术替代和重新设计，观察后续12个月。影响分母是观察期内原计划交付、投产或投入运行的同Scope单位。多个批次分别计算并采用证据支持的最高运营风险档：

```text
0：明确证明没有下游影响
1：延迟<1个月且影响<5%，并存在轻微正向后果
2：延迟1–3个月，或影响5%–10%
3：延迟>3个月、影响>10%或明显性能下降
4：结构性硬阻断整个原子Scope
```

合并规则：结构测试pass为exact 4；fail使用运营档0–3；unknown以运营档为下界、4为上界。临时停产最高3分。库存完全吸收Top1的90天停供且有完整证据时为exact 0；8%同Scope出货延迟2个月为exact 2。

## 5. Effective Supply Concentration

统一有效产出口径：

```text
effective_output_i
= nameplate_i × yield_i × qualification_fraction_i
× target_scope_allocation_i × availability_i

effective_share_i = effective_output_i / Σeffective_output
N_eff = 1 / Σeffective_share_i²
```

Failover压力情景与Downstream一致：最大有效供应商100%失效、窗口90天、分母为同Scope同期间目标需求。分子只计算其他同路线、已认证供应商在90天内可追加且未占用的产出；不得重复计算其基线供应，也不计库存、未认证产能、未来建厂或计划扩产。

`N_eff`、Top1与Failover三个0–4子分分别以30%、30%、40%合成：

```text
C = 0.30*S_Neff + 0.30*S_Top1 + 0.40*S_Failover
```

采用half-up：`[0,0.5)->0`、`[0.5,1.5)->1`、`[1.5,2.5)->2`、`[2.5,3.5)->3`、`[3.5,4]->4`。Top1子分=4且Failover子分=4时强制exact 4。组件是区间时分别计算上下界；缺失组件按`[0,4]`进入，禁止补中性值。

## 6. Capacity Inelasticity

目标增量绑定同一Top1压力情景：

```text
ΔQ_required = max(0, Q_Top1_loss - Q_90-day_failover)
```

数量统一为与目标需求相同的稳定月度产出单位。`ΔQ=0`且证据完整为exact 0。物理时钟从as-of date开始，到新增产线连续3个完整月达到`ΔQ`稳定合格良品，以第三个月结束时评分。排除新供应商客户认证时间，避免与Qualification Barrier重复；包含厂房、设备交付安装、人员、能源、原材料、调试、良率爬坡和制造放行。

扩产任务组成DAG：串行相加、并行取最长路径，由关键路径决定完成时间：

```text
0：≤3个月
1：>3–6个月
2：>6–12个月
3：>12–<18个月
4：≥18个月，且关键路径至少有两类独立物理约束
```

若已证明`≥18个月`但仅证明一类物理约束，必须输出`[3,4]`，不得exact 3或4。

## 7. Substitute Weakness

同规格、同技术路线第二供应商只进入Concentration/Failover。Substitute只包括不同产品、材料、工艺、系统架构、软件减量、绕行或垂直整合路线。路线集合由Stage 2替代假设、已有证据和标准路线分类播种，并执行正向可行性搜索，主动寻找量产、认证、产能和客户采用证据。

连续两轮发现搜索无新增material route且所有路线已解析，集合才可冻结；查询、时间或成本预算耗尽只能返回`incomplete`。

```text
route_ready_date = max(physical_capacity_ready,
                       qualification_ready,
                       ecosystem_ready)

Coverage = min(1,
  Σ mutually_exclusive_qualified_output / total_target_demand)
```

共享产线、原材料、产出池和重复客户分配必须以约束池或最大可行流去重：

```text
0：Production替代覆盖≥30%
1：Ready/Production覆盖10%–<30%
2：存在credible challenge，但Ready覆盖<10%
3：最高仅为原型、样品、测试或未兑现扩产
4：搜索协议完成，且每条冻结路线均有明确失败、不合格、
   取消、时间窗口外或产能不足证据
```

任一material route为`unresolved`时禁止exact 4；完全无法约束时保持`unknown [0,4]`。空集合不能产生4分，除非每个标准路线分类都有明确负面证据。

## 8. Stage 3与Stage 4证据边界

Stage 3可以使用供应商有效产出、份额、良率、认证状态、Scope分配、可用余量、物理扩产周期和替代路线事实计算Segment系统风险，但评分Scope必须保持`company_id=None`。Claim/Card的`subject_company_ids[]`只做事实归属，不能生成公司护城河、收入、利润或投资结论。

Stage 4可以复用不可变的SourceIdentity、SourceVersion、Quote offsets和Quote hash，但必须建立新的CompanyScope Claim/Card，绑定`company_id`、产品、Segment、平台、地区、期限和as-of date，并保存`derived_from_evidence_id`与SourceVersion lineage。再绑定后的证据默认`context_only`且`scoring_eligible=false`；会计、客户认证、客户采用、产能分配和实际替代Business Fact必须完成公司级能力复核后才能成为决定性证据。

Stage 4新证据不得原地修改同一Run的Stage 3结果。可能改变Segment评分时必须生成`segment_recompute_required`，由新的Canonical Stage 3重算产生新版本。

每个存在`bottleneck_owner`的原子Segment Scope必须有typed ChallengerSet，使用完整AssessmentScope和`included_assessment_ids`，并保存排除项、原因和搜索回执。Included可为空，但只有搜索完成且Coverage Gate通过才有效。预算耗尽、集合缺失或unresolved路线进入`COMPANY_ASSESSMENT_INCOMPLETE`：持久化进度、不生成Competition State、不进入Stage 5，并从已完成回执恢复而不重复查询。

## 9. 实现、迁移与冻结门

v1.5拥有独立机器合同、Prompt、Validator和Schema。v1.4的文档、Prompt、机器资产、测试和历史结果保持只读；v1.5字段与评分不得反向迁移到旧结果。当前默认生产配置仍是v1.4，显式`enable_v15_company_chain=true`才启用Stage 4新完成语义。

冻结验收要求：Hard Gate一致率≥95%，Final State一致率≥90%，exact/exact至少10对，线性加权kappa≥0.70，高风险单边升级=0，全部新边界测试、HBM回归和Top-10复核通过。Golden至少包含HBM/先进封装和一个跨行业案例；Holdout必须使用未污染案例。

Coverage Receipt必须记录精确commit SHA、clean/dirty状态、v1.5合同版本，以及PRD、机器合同、Prompt、Validator、Golden和Holdout资产SHA。在人类Golden/Holdout/HBM/Top-10门未真实通过前，本合同必须保持`freeze_candidate`。
