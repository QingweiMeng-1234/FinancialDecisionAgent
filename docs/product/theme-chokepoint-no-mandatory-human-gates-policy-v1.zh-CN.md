# Theme Chokepoint 无强制Human门禁治理策略 v1

| 字段 | 值 |
|---|---|
| Policy ID | `theme-chokepoint-no-mandatory-human-gates-v1` |
| 生效日期 | 2026-08-23 |
| 状态 | `active_permanent` |
| 机器策略 | `config/theme_chokepoint_human_gate_policy_v1.json` |

## 1. 决策

从本策略生效起，Theme Chokepoint 的评测、Calibration/Golden、Holdout评估、Badcase闭环、冻结决定和CI发布不得再把Human标注、Human签名、Human双盲回执、Human仲裁或Human Top-10批准作为必需门禁。

Human产物仍可存在，但只能标记为`optional_advisory_only`。缺失、未签署或与自动结果不一致时，不得改变自动门的Pass/Fail，也不得阻止发布。

## 2. 优先级与历史边界

本策略覆盖生效日及之后的新评测清单，优先于旧迁移计划、旧评测清单或合同冻结段落中要求人工Golden、双盲、两份receipt、人工仲裁或人工Top-10批准的内容。

历史合同、原始submission、Gold、Holdout、仲裁、清单和SHA-256保持不可变。本策略不追溯改写历史结论，只改变向前的冻结治理。

本策略不修改Ordinal锚点、Bound、Hard Gate、State、Scope、时间语义或任何评分公式。

## 3. 唯一强制门禁

冻结与CI只读取机器策略中的`required_automated_gates`，包括：Schema与机械状态校验、输入及资产哈希、模型对固定Reference的一致率、exact/exact样本量与Kappa、高风险单边升级、重复性、无充分证据、时间截断、Badcase资产完整性和Holdout隔离。

固定Reference可以来自历史冻结资产、确定性规则输出或版本化模型辅助资产。报告必须称为`model-to-fixed-reference`，不得在没有独立证据时称为Human Gold、人类可靠性或人—人一致率。

## 4. 永久性

未来策略可以增加可选诊断，但不得恢复强制Human门禁。只有Owner显式发布新的机器可读策略并声明撤销或取代本策略，才可改变这一规则。

## 5. 当前版本影响

取消Human门禁不会自动使当前v1.6评测冻结。Final State一致率、exact/exact样本量、高风险单边升级或其他自动门未通过时，仍必须保持`not_frozen`。不得用取消Human门禁绕过自动失败。
