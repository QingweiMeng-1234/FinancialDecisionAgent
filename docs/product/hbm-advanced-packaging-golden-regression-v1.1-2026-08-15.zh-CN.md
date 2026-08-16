# Theme Chokepoint Scoring v1.1 Golden 边界回归报告

## 1. 结论

| 字段 | 值 |
| --- | --- |
| contract_id | `theme-chokepoint-scoring-v1.1` |
| Golden主题 | AI加速器—HBM—先进封装 |
| assessment_as_of | 2026-08-15 |
| 回归状态 | `boundary_pass`；不是完整冻结证明 |
| Golden断言 | 15/15通过 |
| 数值边界与状态探针 | 6/6通过 |
| 结构校验 | 通过 |
| 冻结决定 | 不冻结；v1.1保持`freeze_candidate`，等待跨主题正例、双标注与排序/状态校准 |

本回归验证的是评分合同能否拒绝越权推断、保持Scope、执行非补偿性Hard Gate并产生唯一业务状态。它不制造公开证据没有支持的公司总分、严格排名或`durable_chokepoint_owner`正例，也不证明当前线性权重和状态阈值已经跨主题校准。

## 2. 固定输入

| 输入 | SHA-256 | 用途 |
| --- | --- | --- |
| `theme-chokepoint-scoring-contract-v1.1.zh-CN.md` | `C19ECBA25F29FE016598FC78F28DD47CE3A45D0C8527FDED89AB0EEF8C0C1846` | 被验收的设计候选 |
| `hbm-advanced-packaging-golden-claim-set-v0.1.zh-CN.md` | `46F8FC3A89D73E1712BE09FB52F06C18CEE62F4460D1B66696D59F6BA42EE491` | 基础Evidence Card与Claim快照 |
| `hbm-advanced-packaging-golden-claim-set-v0.2.zh-CN.md` | `3BBB0C2EA3B83C9596C48E8D151211F38D486665FAA557BDA7858C09826652A8` | 对v0.1的版本化修订层 |
| `golden-case-source-audit-2026-08-15.md` | `0EB69EA648EBF929E0C68E00CF1E0F0031E653550718C99FEE9276C3F60024A8` | 来源清单、原文定位与证明边界 |

关键固定来源内容哈希包括：Samsung 2026 Q1 PDF `33140E1501207E19741EFA7220B9CD39A4F68BA288854B526AB26A6A17E1DB83`、Samsung 2026 Q2 PDF `E90B8E4829403339206FA77821AEADD71E39A611F3830A4CBCD349321DE9E0CF`、Micron FY2026 Q3 prepared remarks `A3CE62B84A059E35FAE80C2BFD5C89F9AF334193FD6ACEFF698FC3008E7D4C27`。本轮没有重新抓取实时网页，而是对已审计并固定哈希的原文快照做合同回归；来源上线状态和2026-08-15之后的产业变化不在本报告证明范围内。

## 3. 回归前修复的确定性缺口

在宣布通过前，审计发现并关闭以下会导致实现分叉的问题：

1. 定义`early_signal`及Replacement状态资格，明确Production继承Ready，因此Performance失败不能绕过到Realized；
2. 补全Segment的`eligible`、resolved `not_supported`和`state_contract_error`出口；
3. 统一Defensibility与Earnings的正式`primary_state`名称；
4. 将Switching Cost改为无重叠半开区间，并定义成本/停机取较高档；
5. 将Qualified Effective Capacity拆成有效份额和合格备用稀缺两个组件，取`min`，防止高份额掩盖充足备用；
6. 填补Revenue Materiality在公司0.5%–1%、分部2%–5%间的空档，并约束无分母只能形成`[1,4]`；
7. 消除Capacity Readiness的30%边界重叠，并明确嵌套里程碑取最高已证明档；
8. 明确Substitute Weakness中`credible_challenge`与仅原型/测试路线的分界；
9. 补齐Criticality零影响与轻微正向影响的边界；
10. 将PRD的Scope字段、`contract_id`、Golden定位和四套权重同步到v1.1候选合同。

上述修复完成后才执行并记录以下通过结果。

## 4. Golden断言结果

| ID | 固定输入/反例 | 预期 | 结果 | 合同机制 |
| --- | --- | --- | --- | --- |
| G01 | v0.1 `SEG-HBM-06`混合HBM stack与Amkor 2.5D | 旧Claim退役；两类Segment分卡 | Pass | Scope不兼容必须拆卡；Amkor不进入HBM stack替代供给 |
| G02 | Samsung HBM4已量产销售并面向Vera Rubin放量 | 只支持Production/Adoption/未量化收入下限；Performance、Qualification、Ecosystem、Materiality、Displacement不自动通过 | Pass | 单一主要高档归属；收入、量产和平台销售不能替代其他Hard Gate |
| G03 | Micron单季HBM4收入`>1B/41.5B > 2.4%` | `single_period_material`成立；持续份额与Displacement仍unknown | Pass | 连续性要求与Actual Displacement独立 |
| G04 | SK hynix“开发完成、量产准备” | Readiness有下限；Production保持unknown，不能写成已量产或未量产 | Pass | 缺证据为unknown，准备状态不等于事件发生 |
| G05 | Amkor既有HDFO项目high-volume production | 只适用于既有项目；不能迁移到HDFO替代CoWoS关系 | Pass | company+product+segment+customer/platform Scope绑定 |
| G06 | Amkor transcript含歧义`final qualification` | `context_only`、`scoring_eligible=false`，Qualification下限不提高 | Pass | source ambiguity不是可信来源冲突，也不是评分边界 |
| G07 | 合成反例：Performance=0，其余Replacement加权维度很高 | Ready、Production、Realized和Scaled均不能通过 | Pass | Hard Gate不可由总分补偿；Realized继承Production→Ready |
| G08 | 合成反例：Segment六维全unknown，`score_max=100` | withheld；不能进入Watch | Pass | Discovery Gate要求正向需求、关键性和供应摩擦原文 |
| G09 | 合成反例：Defensibility Coverage表面充足，但Customer Sourcing unknown | `eligible=false`, `primary_state=null` | Pass | Customer Sourcing是状态资格强制字段 |
| G10 | 合成反例：收入和兑现时间较强，但Margin Transmission unknown | Earnings withheld，不得输出Moderate/Material | Pass | 三个财务强制字段均需resolved |
| G11 | 合成反例：High Defensibility且存在受支持Realized Replacement | `vulnerable_incumbent` | Pass | Competition优先级中实际Displacement最高 |
| G12 | 新发现的原文属于旧期间事实补录 | `assessment_revised`，不能`strengthening` | Pass | `knowledge_revision`与`industry_event`分轴 |
| G13 | 复合锚点有一个required谓词unknown/conflicted/stale/out-of-scope | 该档不能形成下限 | Pass | Condition Coverage只作诊断，不能按比例通过 |
| G14 | 同一管理层披露产生多个页面、段落、新闻稿或转载 | 独立证据计数保持1 | Pass | `origin_event_id/evidence_family_id`去重 |
| G15 | Displacement有3分下限，但Realized六门任一低于3或unknown | 不能进入Realized/Scaled Replacement | Pass | 六项不可补偿3分门 |

## 5. 数值边界和状态完备性

确定性探针对每个临界点左右取值，要求“恰好命中一个档位”或按合同产生唯一状态：

| ID | 检查 | 结果 |
| --- | --- | --- |
| B01 | Demand Pressure：0%、5%、10%、20%及相邻值 | Pass |
| B02 | Qualification/Capacity duration：3、6、12、18个月及相邻值 | Pass |
| B03 | Switching Cost：1%、5%、10%、20%及相邻值；成本/停机跨档取较高档 | Pass |
| B04 | Qualified Effective Capacity公司份额：20%、30%、50%、70%及相邻值 | Pass |
| B05 | 合格备用产出：0%、10%、20%、30%及相邻值 | Pass |
| S01 | 3种Defensibility状态 × 4种挑战者层级（none/credible/ready/realized） | Pass；12种组合均产生唯一Competition状态 |

探针总计21项：Golden 15项加边界/状态6项，`PASS=21, FAIL=0`。

## 6. 文档结构校验

| 检查 | 结果 |
| --- | --- |
| Segment权重 | `15+20+15+15+15+20=100` |
| Defensibility权重 | `20+15+15+20+15+15=100` |
| Replacement权重 | `20+15+15+15+15+10+5+5=100` |
| Earnings权重 | `20+15+15+20+10+10+5+5=100` |
| PRD与合同四套维度/权重 | 完全一致 |
| 合同JSON示例 | 1/1可解析，`contract_id`正确 |
| PRD JSON示例 | 7/7可解析 |
| Markdown围栏 | 合同54个、PRD 40个，均成对闭合 |
| 必要状态枚举 | Segment、Defensibility、Replacement、Earnings、Competition均存在正式名称和唯一优先级 |

## 7. 边界回归的证明范围

- 本报告证明合同对固定Golden输入的语义行为，不证明运行时代码已经实现，也不证明部署、定时监控或用户可见交付。
- Samsung的产品收入/份额、Micron的持续份额、SK hynix的生产/采用、Amkor到CoWoS的同产品替代关系仍需新原文；unknown不能因边界回归通过变成0分或中性分。
- 当前Golden主要提供边界、反例和withheld正例，不足以校准跨行业分数分布。AI数据中心电力/冷却是重新冻结前的必做主题。
- 实现阶段必须把本报告15条Golden断言和区间/状态探针转成可执行测试；运行实现尚不存在，因此本轮不声称代码测试通过。

## 8. 状态判定

`theme-chokepoint-scoring-v1.1`通过HBM边界回归，但不满足完整冻结门槛，状态为`freeze_candidate`。恢复`frozen`前必须完成合同第17.1节的跨主题正例、双标注、分歧裁决、混淆矩阵、排序校准和产品阈值批准。
