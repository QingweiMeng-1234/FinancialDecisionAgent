# AI 加速器—HBM—先进封装 Golden Claim Set v0.2 修订层

## 1. 文档状态

| 字段 | 值 |
| --- | --- |
| 文档类型 | Golden Case 版本化修订层 |
| 基础版本 | `golden-claim-set-hbm-advanced-packaging-v0.1` |
| 对应评分合同 | `theme-chokepoint-scoring-contract-v1.1.zh-CN.md` |
| 版本 | `golden-claim-set-hbm-advanced-packaging-v0.2` |
| 截止日期 | 2026-08-15 |
| 当前状态 | `versioned_boundary_input`；Claim覆盖语义已固定，公司总分、正向最终标签和v1.1权重/门槛未由本文件冻结 |
| 回归结果 | `hbm-advanced-packaging-golden-regression-v1.1-2026-08-15.zh-CN.md`；仅证明边界反例 |

本文件不静默覆盖 v0.1。读取 Golden Case 时，未在本文件修改的 Evidence Card 和 Claim 继续继承 v0.1；发生冲突时以本文件为准。v0.1 保留为历史快照，不再作为 v1.1 的单独验收输入。

本轮只修正以下结构问题：

1. 废弃单一成熟度总序，改用带 Scope 的多轴里程碑；
2. 拆分 HBM stack 与 2.5D integration 的替代路线；
3. 把“未证明”从业务否定改为 `unknown`；
4. 增加 Samsung 2026 Q1/Q2 官方财报证据；
5. 将 Amkor `final qualification` 转录歧义降为人工复核上下文；
6. 将实际商业化、规模化供给和实际 displacement 分开验收。

## 2. v0.2 适用合同

所有新旧 Claim 均按以下结构解释：

```yaml
evidence_state: supported | conflicted | unknown
bound_type: exact | lower_bound | upper_bound | interval | none
rating_min: 0..4
rating_max: 0..4
primary_scoring_dimension:
secondary_uses:
source_ambiguity: false
human_review_required: false
```

公司级评估必须绑定：

```text
company_id
+ product_id
+ segment_id
+ customer_or_platform_scope
+ geography
+ time_horizon
+ as_of_date
```

禁止从某一产品、客户或项目的里程碑推断另一个 Scope 的状态。

## 3. 新增官方来源

| source_id | 来源 | publication_date | retrieved_at | 版本标识 | 说明 |
| --- | --- | --- | --- | --- | --- |
| `HBM-09` | [Samsung Electronics 2026 Q1 Earnings Presentation](https://images.samsung.com/is/content/samsung/assets/global/ir/docs/2026_1Q_conference_eng.pdf) | 2026-04-30 | 2026-08-15 | PDF SHA-256 `33140E1501207E19741EFA7220B9CD39A4F68BA288854B526AB26A6A17E1DB83` | Samsung 官方财报材料；具名 NVIDIA Vera Rubin 平台并证明 HBM4 量产销售 |
| `HBM-10` | [Samsung Electronics 2026 Q2 Earnings Presentation](https://images.samsung.com/is/content/samsung/assets/global/ir/docs/2026_2Q_conference_eng.pdf) | 2026-07-30 | 2026-08-15 | PDF SHA-256 `E90B8E4829403339206FA77821AEADD71E39A611F3830A4CBCD349321DE9E0CF` | Samsung 官方财报材料；证明 HBM4 销售继续放量，但未披露 HBM4 独立收入和客户内份额 |

## 4. 新增 Evidence Cards

### `EV-HBM-18`

- `claim_id`: `DEF-SAM-01-V02`, `REP-SAM-01-V02`
- `source_id`: `HBM-09`
- `content_sha256`: `33140E1501207E19741EFA7220B9CD39A4F68BA288854B526AB26A6A17E1DB83`
- `exact_quote`: “Commenced industry’s first mass product sales of HBM4 and SOCAMM2 for NVIDIA Vera Rubin platform”
- `page/section/text_offset`: PDF p.7, Memory, 1Q 2026 Results
- `publisher`: Samsung Electronics
- `source_type`: Official quarterly earnings presentation
- `publication_date`: 2026-04-30
- `data_as_of`: 2026 Q1
- `retrieved_at`: 2026-08-15
- `stance`: `supports`
- `primary_scoring_dimension`: `customer_adoption`
- `secondary_uses`: `production` as `floor_only`; `financial` as `floor_only`
- `limitations`: 证明面向具名 NVIDIA Vera Rubin 平台的 HBM4 量产销售；不能单独证明 NVIDIA 正式认证范围、Samsung 在该平台的采购份额、HBM4 独立收入金额或从其他供应商迁移。

### `EV-HBM-19`

- `claim_id`: `REP-SAM-02-V02`
- `source_id`: `HBM-10`
- `content_sha256`: `E90B8E4829403339206FA77821AEADD71E39A611F3830A4CBCD349321DE9E0CF`
- `exact_quote`: “Scaled up HBM4 sales with industry-leading performance”
- `page/section/text_offset`: PDF p.7, Memory, 2Q 2026 Results
- `publisher`: Samsung Electronics
- `source_type`: Official quarterly earnings presentation
- `publication_date`: 2026-07-30
- `data_as_of`: 2026 Q2
- `retrieved_at`: 2026-08-15
- `stance`: `supports`
- `primary_scoring_dimension`: `customer_adoption`
- `secondary_uses`: `financial` as `floor_only`; `performance_parity` as `context_only`
- `limitations`: 证明 HBM4 销售在下一季度继续放量。`industry-leading performance` 是供应商自述，缺同范围可比测试，不能单独通过 Performance Gate；没有 HBM4 收入金额、统一市场分母或采购迁移。

### `EV-HBM-20`

- `claim_id`: `EARN-MU-03-V02`
- `source_id`: `HBM-02`
- `content_sha256`: `A3CE62B84A059E35FAE80C2BFD5C89F9AF334193FD6ACEFF698FC3008E7D4C27`
- `exact_quote`: “Total fiscal Q3 revenue was $41.5 billion”
- `page/section/text_offset`: PDF p.7, Revenue
- `publisher`: Micron Technology
- `source_type`: Official earnings prepared remarks
- `publication_date`: 2026-06-24
- `data_as_of`: FY2026 Q3
- `retrieved_at`: 2026-08-15
- `stance`: `supports`
- `primary_scoring_dimension`: `issuer_materiality`
- `secondary_uses`: none
- `limitations`: 与 v0.1 `EV-HBM-05` 的“over $1 billion in HBM4 revenue”组合后，可证明该季度 HBM4 收入占公司收入超过 2.4%，达到 1% 候选阈值；只有一个期间，不能证明连续性或持续份额提升。

## 5. 失效和拆分的旧 Claim

### 5.1 退役 `SEG-HBM-06`

v0.1 `SEG-HBM-06` 同时使用 Samsung/Micron HBM 与 Amkor HDFO，跨越 `seg_hbm_stack` 和 `seg_2_5d_integration`，自 v0.2 起标为：

```yaml
claim_id: SEG-HBM-06
status: retired_scope_error
scoring_eligible: false
```

替换为两个独立 Claim：

| claim_id | assessment_scope | Claim | evidence_id | 允许结论与限制 |
| --- | --- | --- | --- | --- |
| `SEG-HBM-STACK-06-V02` | `seg_hbm_stack` | Samsung 与 Micron 的 HBM4 商业量产/收入证明同范围竞争供给真实存在。 | `EV-HBM-05`, `EV-HBM-09`, `EV-HBM-18`, `EV-HBM-19` | 只能反驳“没有其他商业供给”；不能据此计算客户批准后的有效集中度、可售余量或同一 GPU/ASIC 的可互换性。 |
| `SEG-2.5D-06-V02` | `seg_2_5d_integration` | Amkor HDFO 既有项目已高量产，证明 2.5D/HDFO 路线不是纯概念。 | `EV-HBM-11` | 只证明 HDFO 自身商业生产；不能证明它与 CoWoS 在同一芯片、客户和封装规格上可替换。 |

Amkor 不得降低 HBM stack 的 Effective Supply Concentration；Samsung/Micron HBM 也不得证明 2.5D integration 已有可用替代产能。

### 5.2 退役单一成熟度断言

以下 v0.1 断言不再执行：

```text
maturity(Samsung_HBM4) == mass_production
maturity(Samsung_HBM4) != material_revenue
maturity(Micron_HBM4) == material_revenue
maturity(Micron_HBM4) != sustained_share_gain
```

原因是 `!=` 把“公开证据未证明”错误表达为业务否定，同时把生产、财务和份额压成一条总序。

## 6. v0.2 多轴 Golden 预期

### 6.1 Samsung HBM4

```yaml
scope:
  company_id: samsung_electronics
  product_id: hbm4
  segment_id: seg_hbm_stack
  customer_or_platform_scope: nvidia_vera_rubin
  as_of_date: 2026-08-15

production:
  state_min: mass_production
  evidence_state: supported
  bound_type: lower_bound

adoption:
  state_min: production_use
  evidence_state: supported
  bound_type: lower_bound

financial:
  state_min: realized_unquantified
  evidence_state: supported
  bound_type: lower_bound

issuer_materiality:
  evidence_state: unknown
  bound_type: none

segment_materiality:
  evidence_state: unknown
  bound_type: none

market_presence:
  evidence_state: unknown
  bound_type: none

share_trajectory:
  evidence_state: unknown
  bound_type: none

displacement:
  evidence_state: unknown
  bound_type: none
```

Q1 的量产销售和 Q2 的销售放量不能单独通过 Performance、Qualification、Ecosystem 或 Actual Displacement Gate。因此对“Samsung 在 Vera Rubin 上替代原供应商”的关系，不得仅凭上述证据输出 `replacement_ready`、`realized_replacement` 或 `scaled_replacement`。

### 6.2 Micron HBM4

```yaml
scope:
  company_id: micron
  product_id: hbm4_12_high
  segment_id: seg_hbm_stack
  as_of_date: 2026-06-24

production:
  state_min: ramp
  evidence_state: supported
  bound_type: lower_bound

financial:
  state_min: single_period_material
  evidence_state: supported
  bound_type: lower_bound

issuer_materiality:
  numerator: "> USD 1.0B HBM4 revenue"
  denominator: "USD 41.5B total fiscal Q3 revenue"
  ratio_lower_bound: 0.024
  evidence_state: supported
  bound_type: lower_bound

share_trajectory:
  evidence_state: unknown
  bound_type: none

displacement:
  evidence_state: unknown
  bound_type: none
```

这组证据支持 Micron 是有规模的商业替代供给，但收入可能来自市场增量。没有同客户、同产品的采购迁移或严格份额三角验证时，不得输出 `realized_replacement`。

### 6.3 SK hynix HBM4

v0.1 的“开发完成、量产准备”继续有效，但不得精确断言公司真实状态停在 `prototype`。v0.2 预期：

```yaml
product_readiness:
  state_min: prototype
  evidence_state: supported
  bound_type: lower_bound

production:
  evidence_state: unknown
  bound_type: none

qualification:
  evidence_state: unknown
  bound_type: none

adoption:
  evidence_state: unknown
  bound_type: none
```

“量产准备”只能形成 readiness 下限，不能形成“尚未量产”的业务否定。

### 6.4 Amkor HDFO

必须拆成三个 Scope：

| Scope | 可证事实 | 预期边界 |
| --- | --- | --- |
| HDFO 既有项目自身 | 多客户 high volume production | `production >= mass_production`，只适用于既有项目 |
| 两个 AI 数据中心项目 | transcript 出现 `final qualification` 表述 | 仅作方向性上下文，等待人工复核 |
| HDFO 替代 CoWoS 关系 | 有客户参与和平台进展 | `credible_challenge` 候选；Performance、Qualification、Ecosystem、Displacement 仍不足 |

`EV-HBM-12` 自 v0.2 起覆盖为：

```yaml
source_quote_state: supported
evidence_state: unknown
bound_type: none
source_ambiguity: true
human_review_required: true
scoring_role: context_only
scoring_eligible: false
```

`source_quote_state: supported` 只表示原文中确实存在该句；语义 Claim 本身仍为 `evidence_state: unknown`。人工消歧或获得正式书面材料前，不得据此提高 Qualification 评分下限。

## 7. `actual_displacement_evidence` Golden Gate

Samsung、Micron、SK hynix 或 Amkor 只有满足以下任一证明路径，才能进入 `realized_replacement`：

1. 客户或供应商原文明确确认同一产品/平台上的替换、移除、供应份额迁移、second-source 转主供或设计路线切换；
2. 同产品、同分母至少三个可比时点，incumbent 连续下降且 challenger 连续上升，并有证据排除双方仅随总市场扩张的解释。

以下事实全部不足以单独通过 Gate：

- 已量产；
- 已商业出货；
- 收入达到公司或分部重要性阈值；
- 市场存在达到 5%；
- 单次份额上升；
- 多家供应商同时存在；
- 新产品面向某个平台销售但未披露供应迁移。

## 8. v0.2 Agent 验收断言

```text
ASSERT segment_claim(SEG-HBM-06).scoring_eligible == false

ASSERT Amkor_HDFO NOT IN alternatives(seg_hbm_stack)

ASSERT production(Samsung_HBM4, NVIDIA_Vera_Rubin).state_min == mass_production
ASSERT adoption(Samsung_HBM4, NVIDIA_Vera_Rubin).state_min >= production_use
ASSERT issuer_materiality(Samsung_HBM4).evidence_state == unknown
ASSERT share_trajectory(Samsung_HBM4).evidence_state == unknown
ASSERT displacement(Samsung_HBM4, NVIDIA_Vera_Rubin).evidence_state == unknown

ASSERT issuer_materiality(Micron_HBM4_FY2026Q3).ratio_lower_bound > 0.01
ASSERT share_trajectory(Micron_HBM4).evidence_state == unknown
ASSERT displacement(Micron_HBM4).evidence_state == unknown

ASSERT production(SK_hynix_HBM4).evidence_state == unknown
  WHEN only development-complete and mass-production-readiness evidence is available

ASSERT qualification(Amkor_AI_HDFO).rating_min IS NOT INCREASED_BY EV-HBM-12
  WHEN source_ambiguity == true AND human_review_required == true

ASSERT replacement_state(candidate) != realized_replacement
  WHEN actual_displacement_evidence.evidence_state != supported

ASSERT evidence_update.event_type == knowledge_revision
  WHEN newly found source predates the previous comparable industry snapshot

ASSERT anchor(candidate, level=k).pass == false
  WHEN any required predicate for level k is unknown, conflicted, stale, or out_of_scope

ASSERT independent_evidence_count(origin_event_id, evidence_family_id) == 1
  WHEN multiple pages, transcript paragraphs, press releases, or media articles derive from the same original disclosure

ASSERT replacement_state(candidate) NOT IN {realized_replacement, scaled_replacement}
  WHEN any of Performance, Qualification, Capacity, Adoption, Ecosystem, or Displacement has rating_min < 3 or is not supported
```

任一断言失败时，v1.1评分器不得通过本Golden Case，即使加权总分超过状态阈值。这15条断言只构成边界回归；不能替代跨主题正例、双标注和排序校准。

## 9. 仍为证据缺口、但不阻止合同冻结的部分

- Performance、Qualification、Capacity、Ecosystem 的数值硬门槛已由v1.1合同冻结；当前公司卡因对应原文不足而保持unknown/withheld，不得用合同阈值反推事实；
- Samsung 在 NVIDIA Vera Rubin 中的认证范围和采购份额仍未知；
- Micron HBM4 的连续 issuer/segment materiality 和同口径市场份额仍待补；
- 任一 HBM 供应商的 `sustained_gain` 仍缺三个可比时点和两个连续提升区间；
- Amkor AI 数据中心项目的正式 qualification 状态仍需无歧义来源；
- HDFO 与 CoWoS 的同产品性能、TCO、生态迁移和实际 displacement 仍未知；
- 当前仍不能形成严格公司竞争力排名或 `durable_chokepoint_owner` 正例；这是Golden应拒绝的越权结论，不是补零或强行构造正例的理由。
