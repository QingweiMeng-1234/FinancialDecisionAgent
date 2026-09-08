# Theme Chokepoint v1.1 重新冻结评估 v0.1

## 1. 决定

**不重新冻结。** `theme-chokepoint-scoring-v1.1`继续保持 `freeze_candidate`。

本轮已经补齐 AI 数据中心电力/冷却 Golden 和三个真实正例，但没有满足“至少两名标注员彼此不可见结果的独立标注”以及 Top-10 产品负责人实际复核。因此不能把文档完备误报成合同已冻结。

## 2. Gate 状态

| 重新冻结 Gate | 状态 | 证据/剩余动作 |
| --- | --- | --- |
| 1. HBM 15 条边界断言全部通过 | Pass | 既有 HBM v1.1 回归已记录 15/15；本轮未改 HBM 输入 |
| 2. 完成电力/冷却 Golden；补真实 Durable、Realized、Weak Earnings | Pass | 10 张主题卡；Modine 当前 Weak；补充 ASML Durable 和 Apple Realized/Scaled |
| 3. 至少两名标注员盲标同一固定样本 | **Fail** | 当前只有同 Agent 双 pass；需要外部第二标注员 |
| 4. 保存分歧、裁决、混淆矩阵 | Partial | 同 Agent 版本已完成；外部标注后必须重算并裁决 |
| 5. Pairwise 与 Top-10 人工校准 | Partial | 12 组 pairwise 无明显反序；用户/产品负责人尚未逐张认可 Top-10，召回率不能计算 |
| 6. 产品负责人批准数值阈值 | Partial | 设计讨论中的 95%/90%/0.70/80% 已写入协议；仍需在外部标注和 Top-10 实测结果上最终签字 |

## 3. 本轮真正完成的内容

1. Discovery Pool 固定为 30 个 scoped 对象，Review 固定为 10 张卡。
2. Evidence Pack 扩充到 35 条原文并完成哈希校验。
3. 补入 DOE/NERC 需求、关键性和变压器长交期原文，避免只用供应商订单证明卡点。
4. 将 Modine 从历史 FY2026 Q2 刷新到 FY2027 Q1，证明 Weak Earnings 是当前状态而非过期事件。
5. 用 ASML EUV 补 Durable 正例，用 Apple Silicon 补 Realized Gate 正例；均限制 Scope 和挑战者集合。
6. 完成同 Agent 双 pass 压测、100% 分歧裁决、混淆矩阵与 12 组 pairwise 校准。

## 4. 下一步最小闭环

1. 把合同、`evidence-pack-v0.1.json`和已去除 expected-label 的`external-annotation-task-v0.1.json`交给一名外部标注员；发出前按`external-validation-runbook-v0.1.zh-CN.md`核对固定输入哈希；
2. 合并外部结果，重算 Hard Gate 一致率、最终状态一致率和 weighted kappa；
3. 由产品负责人逐张审阅 10 张主题卡，标记 `accept/reject/missed_candidate`，计算 Top-10 候选召回；
4. 所有分歧裁决后，若达到 95%/90%/0.70/80% 且 Durable/Realized 误升级为 0，再把合同状态改为 `frozen`并更新 Manifest 哈希。

任何一步未通过，都应修订 Golden 或合同并重新标注，而不是降低阈值。
