# Theme Chokepoint 自动冻结门槛修订 v3

| 字段 | 值 |
|---|---|
| Policy ID | `theme-chokepoint-automated-freeze-thresholds-v3` |
| 生效日期 | 2026-08-24 |
| 状态 | `active` |
| 机器策略 | `config/theme_chokepoint_automated_freeze_thresholds_v3.json` |

## 修订

高风险门由`high_risk_one_sided_upgrades = 0`改名并放宽为：

`high_risk_boundary_disagreements <= 1`

该门是对称边界检查：固定Reference与模型中恰好一方属于`candidate_chokepoint`或`strong_candidate_chokepoint`时计1。它不再被描述为模型“向上升级”，因为模型更保守的降级差异也会被计入。

当前ASML EUV案例中，Reference为`candidate_chokepoint`、模型为`watch_segment`，计1，满足新门槛。

Hard Gate、Final State、Kappa及exact/exact门槛继续使用v2数值。本修订不改写旧指标或旧清单，只对新生成清单生效。
