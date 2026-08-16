# AI 数据中心 Golden 同 Agent 双 Pass 裁决与校准 v0.1

## 1. 性质声明

本文件是**同一 Agent 在固定 Evidence Pack 上进行的两次隔离 pass 压测**。它用于暴露锚点、Scope 和状态派生问题，不是两名独立标注员的盲标结果，不能满足重新冻结 Gate 3。

## 2. 原始一致性

| 指标 | 原始结果 | 候选阈值 | 本轮含义 |
| --- | ---: | ---: | --- |
| Exact 0–4 标签数 | 18 | - | 只纳入 exact 初始标签，区间不强行压成点 |
| 线性 weighted kappa | 0.839 | >=0.70 | 同 Agent 压测通过 |
| 二次 weighted kappa | 0.935 | 观察值 | 仅作敏感度参考 |
| Hard Gate 一致率 | 19/20 = 95.0% | >=95% | 同 Agent 压测刚好通过 |
| 最终状态一致率 | 11/12 = 91.7% | >=90% | 同 Agent 压测通过 |
| Durable/Realized 错误升级 | 0 | 0 | 两个正例均由不可补偿 Gate 支持 |
| 分歧裁决率 | 100% | 100% | 5 个分歧全部裁决 |
| Pairwise 明显反序 | 0/12 | 0 | 仅为内部样本，不替代人工 Top-10 |

混淆矩阵以 A 为行、B 为列，见 `confusion-matrix-v0.1.csv`。线性 weighted kappa 使用 `w(i,j)=1-|i-j|/4`。

## 3. 分歧裁决

| 分歧 | Pass A | Pass B | 最终裁决 | 类型 | 理由 |
| --- | --- | --- | --- | --- | --- |
| ASML Qualification Lock-in | 4 | 3 | `supported lower_bound [3,4]` | 锚点歧义 | 原文明示客户工艺、recipe、critical layer 和多重图形化差异，足以支持重大重新设计下限；没有公开的明确月数，不能 exact 4 |
| Apple Customer Adoption | 4 | 3 | exact 4 | 标注错误 | 三款首发 Mac 加上“Mac transition ... now complete”证明多平台、全产品线生产采用，不只是两个周期的单平台使用 |
| Modine Margin Transmission | 1 | 0 | exact 1 | 锚点歧义 | 毛利率下降 960bp 且原因可归因，但绝对 operating income 和 EBITDA 仍增长；不满足“利润被完全吸收”的 0 档，满足爬坡拖累明显的 1 档 |
| nVent Execution/Delivery | 2 | 1 | exact 2 | 标注错误 | 三年第三次扩产和已新增 40 万平方英尺证明资源与设施已落实；仍不证明 qualification、yield 或产出，因此不能高于 2 |
| Eaton 2027 工厂当前可售产出 Gate | fail | unknown | unknown | 时效/Scope | 2025 原文只给未来计划；截至 2026-08-15 未刷新 qualification 和 output，不能把旧计划自动变成当前明确零产出，也不能算已产出 |
| Vertiv GB300 最终状态 | early_signal | null | `early_signal` | 合同适用 | 产品级参考架构是正向 Primary Claim，Performance Gate 不是 fail，满足 Replacement Discovery；缺 qualification/adoption 只阻止 Credible/Ready，不阻止 Early Signal |

## 4. 三个真实正例的最终裁决

### 4.1 Durable：ASML EUV

- Technical=4、Qualified Effective Capacity=4、Customer Sourcing=4；Qualification=`[3,4]`、Switching=`[2,4]`、Quality/Delivery=`[3,4]`。
- Defensibility `score_min=85.0`，Decision Coverage `0.85`，状态 `high_defensibility`。
- 冻结 challenger set 包括 Canon/Nikon DUV、Canon nanoimprint、中国本土 EUV/SSMB 概念和 DUV multi-patterning；固定包内没有路线达到同范围 `credible_challenge`。
- Competition 状态为 `durable_chokepoint_owner`。结论只限冻结集合和当前期间，不能写成“永久垄断”。

### 4.2 Realized：Apple Silicon 替代 Intel Mac 处理器

- Performance、Qualification、Capacity、Adoption、Ecosystem、Displacement 的下限均 >=3。
- Universal 2、Rosetta 2 和 Big Sur 证明迁移可执行；2020 三款 Mac 首发和 2023 全产品线迁移完成证明生产采用与 displacement。
- `realized_replacement` Gate 正向通过；由于同时满足规模 Gate，唯一 `primary_state=scaled_replacement`，`achieved_gates[]`保留 Realized。

### 4.3 Weak Earnings：Modine Data Centers

- FY2027 Q1 Data Centers 销售 3.486 亿美元，占公司净销售约 39.9%，Revenue Materiality exact 2；收入已确认，Time to Revenue exact 4。
- 分部毛利率 20.2%，同比下降 960bp，原因包括扩产、供应链、生产低效、材料和保修；Margin Transmission exact 1。
- 当季 FCF 为 -500 万美元，原文归因包括 Data Centers 扩产 CapEx；Capital/Cash Burden 为 upper bound `[0,1]`，不是武断 0 分。
- `score_min=38.8`、`score_max=57.5`、Decision Coverage `0.8125`；Margin `rating_max<2`触发不可补偿 Hard Fail，最终 `weak_earnings_capture`。

## 5. Pairwise 校准

12 组成对比较中没有出现“人工预期 A 优于 B、启发式却把 B 排在 A 前”的明显反序。8 组保持 `overlapping` 或跨 Segment 不作严格排序，避免虚假精度。

这只能说明当前小样本没有显著系统性反序，不能证明线性 0–4 权重是经济距离，也不能替代用户/产品负责人的 Top-10 人工认可。

## 6. 仍需外部执行的标注包

外部标注员必须使用相同的：

- `evidence-pack-v0.1.json`；
- 已从`golden-claim-set-v0.1.json`抽离 Scope 且不含 expected labels 的`external-annotation-task-v0.1.json`；
- 合同 `theme-chokepoint-scoring-v1.1`；
- 输出 exact/interval、Hard Gate、唯一状态和 `withheld_reason`。

外部结果返回后重新生成混淆矩阵、weighted kappa、状态错误率和逐项裁决；不得沿用本文件的同 Agent 指标作为独立一致性证明。
