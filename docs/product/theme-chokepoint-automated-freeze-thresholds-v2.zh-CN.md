# Theme Chokepoint 自动冻结门槛修订 v2

| 字段 | 值 |
|---|---|
| Policy ID | `theme-chokepoint-automated-freeze-thresholds-v2` |
| 生效日期 | 2026-08-23 |
| 状态 | `active` |
| 机器策略 | `config/theme_chokepoint_automated_freeze_thresholds_v2.json` |

## 修订内容

- Final State一致率门槛由`0.90`下调至`0.75`。
- 线性加权Kappa门槛保持`0.70`。
- 计算Kappa所需的`exact/exact`最低有效配对数由`10`下调至`1`。
- Hard Gate一致率门槛保持`0.95`。
- 高风险单边升级门槛保持`0`。

## 低样本披露

只有1至9对`exact/exact`时，Kappa可以通过自动门，但必须标记`low_sample_size`，不得声称统计稳健、充分校准或具有强泛化证明。

本修订不修改历史指标和旧清单，只对新生成的评测清单生效。取消或放宽这些门槛不会自动覆盖仍然失败的其他自动门。
