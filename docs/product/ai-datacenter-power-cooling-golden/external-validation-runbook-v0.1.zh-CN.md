# Theme Chokepoint v1.1 外部验证执行说明 v0.1

## 1. 目的

本轮不是继续让同一 Agent 重跑，而是完成两个尚未满足的重新冻结 Gate：

1. 一名未看过预期答案、A/B 结果和裁决文档的第二标注员，对固定 Evidence Pack 独立盲标；
2. 产品负责人对 10 张主题 Review Card 逐张做 `accept/reject/missed_candidate` 判断。

在这两项完成、分歧全部裁决且数值门槛通过前，`theme-chokepoint-scoring-v1.1`必须保持 `freeze_candidate`。

## 2. 给外部第二标注员的文件

只提供以下三个文件：

- `theme-chokepoint-scoring-contract-v1.1.zh-CN.md`
- `ai-datacenter-power-cooling-golden/evidence-pack-v0.1.json`
- `ai-datacenter-power-cooling-golden/external-annotation-task-v0.1.json`

发出前核对固定输入 SHA-256：

| 文件 | SHA-256 |
| --- | --- |
| 评分合同 | `c19ecba25f29fe016598fc78f28dd47ce3a45d0c8527fded89ab0eef8c0c1846` |
| Evidence Pack | `834bcd48e7240b9704c9f20ff86069b95ae257445d7db22b239fa576af730e86` |
| 去答案任务包 | `57a363c6a0cb4d999c7b5109dbf51b309939d4ba6e0cbf0aec5f0b1510e764de` |

可直接交付的三文件盲标压缩包为`outputs/theme-chokepoint-golden-20260816-001/external-annotation-blind-pack-v0.1.zip`，SHA-256 为`799c54b28a14ff85c4da76f73ede4d3a4a3a5265646efe90293d8a9f0a2eb033`。ZIP 内严格只有上述三份输入。

禁止提供或展示以下材料：

- `golden-claim-set-v0.1.json`
- `annotator-a-v0.1.json`、`annotator-b-v0.1.json`
- `adjudication-calibration-v0.1.zh-CN.md`
- `confusion-matrix-v0.1.csv`、`pairwise-ranking-v0.1.csv`
- `refreeze-assessment-v0.1.zh-CN.md`
- 审核 XLSX 中的 Positive Cases、Calibration、Checks 工作表

第二标注员不得联网补证，也不得依据公司名或常识补全缺失事实。固定包没有公开证据时应输出 `unknown + none + [0,4]`；0 分必须有明确失败、退出、未采用或不符合要求的证据。

## 3. 第二标注员输出要求

第二标注员复制 `external-annotation-task-v0.1.json` 的任务 ID，另存为 `external-annotator-result-v0.1.json`，并填写：

- 每个 `Oxx`：`evidence_state`、`bound_type`、`rating_min/rating_max`、主要证据 ID、`withheld_reason` 和一句原文约束理由；
- 每个 `Hxx`：`pass/fail/unknown`、证据 ID、`withheld_reason` 和理由；
- 每个 `Sxx`：唯一 `primary_state` 或 `null`、`achieved_gates[]`、`withheld_reason` 和理由。

区间不能为计算方便压成单点；`supported` 也可以是 lower/upper/two-sided bound。成熟度只做 Gate，不作为额外加分维度。同一原子事实只能有一个主要高档计分归属，但允许 `floor_only/context_only` 复用。

## 4. 产品负责人复核 10 张卡

打开 `outputs/theme-chokepoint-golden-20260816-001/theme-chokepoint-golden-audit-v0.1.xlsx` 的 `Review Cards` 工作表，逐张填写：

- `Accept`：这张卡值得进入最多 10 家的人工复核队列；
- `Reject`：证据或 Scope 不足，不应进入；
- `Missed Candidate`：Golden 的 Discovery Pool 中存在更应进入 Top-10、但当前未进入的候选；同时记录候选名和理由。

不要根据总分机械 Accept。重点检查产品锚点是否一致、原文能否支撑区间、卡点是否传导到收入/利润，以及高分是否来自重复计分。

Top-10 召回率的验收口径为：

```text
recall_at_10 = 当前 Top-10 中被产品负责人 Accept 的候选数
               / 产品负责人确认应进入 Top-10 的全部候选数
```

分母包括明确的 `Missed Candidate`。若分母为 0，本轮指标为不可计算，不得按 100% 处理。

## 5. 回收后的裁决与重新冻结

回收外部 JSON 和 Review Cards 后：

1. 对齐 item_id，检查缺失、重复和非法状态；
2. 重新计算 Hard Gate 一致率、最终状态一致率、线性 weighted kappa、Durable/Realized 错误升级数与 Top-10 召回率；
3. 对每个分歧标注 `annotation_error/source_ambiguity/scope_error/anchor_ambiguity/contract_defect`，100% 裁决；
4. 仅在 Hard Gate `>=95%`、最终状态 `>=90%`、weighted kappa `>=0.70`、Top-10 recall `>=80%`、Durable/Realized 错误升级为 0，且重复高档计分与同源误计均为 0 时，才把合同和 Manifest 改为 `frozen`；
5. 任一 Gate 未通过，修订 Golden 或合同后重新盲标，不能降低阈值或沿用旧指标。

同一 Agent 双 pass 的 95.0%/91.7%/0.839 只能作为内部压力测试，不能填入外部独立一致性 Gate。
