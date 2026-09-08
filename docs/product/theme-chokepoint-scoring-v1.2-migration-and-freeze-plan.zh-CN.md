# Theme Chokepoint Scoring v1.2 迁移与重新冻结计划

## 1. 决策

| 字段 | 值 |
| --- | --- |
| decision_id | `theme-chokepoint-scoring-v1.2-migration-20260816` |
| candidate_contract | `theme-chokepoint-scoring-v1.2` |
| 状态 | `freeze_candidate` |
| 前版 | `theme-chokepoint-scoring-v1.1`，保持不可变 |
| 触发事件 | v0.3双盲预裁决一致性门失败及20项分歧裁决 |
| 实现授权 | 无；本轮只修改合同与验证资料，不修改运行代码 |

v0.3的Hard Gate一致率为29/37（78.38%），低于95%；真实exact/exact仅6对，低于10对，因此即使样本内线性加权kappa为1.0，也不能通过样本数门。裁决完成率100%只说明分歧已被处理，不能追溯改变预裁决一致性。

## 2. v1.2规范变更

| change_id | v1.1问题 | v1.2决定 | 迁移行为 |
| --- | --- | --- | --- |
| C12-01 | `capital_intensity`与`capital_cash_burden`并存 | 评分字段唯一为`capital_cash_burden` | 旧别名只写迁移日志；新payload出现旧名即拒绝 |
| C12-02 | 明确撤回的挑战者会落入`early_signal` | 新增负向终态`failed_or_withdrawn`，状态优先级最高 | 历史记录不回写；新评估按原子产品代际重建 |
| C12-03 | O85/O86错误进入Segment family | 新增非加权`earnings_overlay`命名空间 | 不进入Segment score或Coverage |
| C12-04 | 当前Gaudi与继任Falcon Shores被OR Gate拼接 | 一卡一个原子产品代际，跨代Scope为schema错误 | 拆卡并保留lineage，禁止公司级OR代替产品状态 |
| C12-05 | exact/lower/upper/unknown选择仍依赖解释 | 冻结确定性区间求值顺序 | exact必须同时有下限和排除更高档的上界 |
| C12-06 | Hard Gate对复合谓词的unknown传播不清 | 冻结pass/fail/unknown真值表 | 保存逐谓词与决定性Claim，禁止unknown折算 |
| C12-07 | 当前事实与未来承诺可被混合升级 | 新增`evidence_time_semantics` | 未来GW、ramp和目标不得证明当前产出/采用/收入 |
| C12-08 | 绝对订单可能被当作Demand增长 | 无同Scope增长分母时Demand保持unknown | 只允许`positive_demand_presence`支持Discovery |
| C12-09 | Customer Adoption 2–4档主体与持续性不够机械 | 2=一个具名生产客户；3=两个具名生产客户或一个跨两期；4=多具名平台且有规模和连续性 | v0.4必须按固定原文验证边界 |
| C12-10 | 后续官方recast可能被误标为source conflict | 官方更正按lineage覆盖旧口径 | 保留版本链，不自动输出conflicted |

## 3. 不迁移的历史资产

以下文件保持原样，任何v1.2工具不得覆盖：

- v1.1合同及其决策记录；
- v0.1、v0.2、v0.3盲包、标注结果、submission receipt、agreement与裁决记录；
- v0.3两份原始标注SHA及原始agreement SHA；
- 已解盲的HBM、电力/冷却Golden及公司案例。

历史结果只用于失败诊断和回归边界，不能进入v0.4盲标样本。

## 4. v0.4预承诺

### 4.1 样本

- 全部案例必须从未进入此前盲标、裁决或Golden人工审核；
- 每张卡只包含一个原子产品代际；
- 至少16个`exact_capable=true`条目，目标产生至少10对真实exact/exact；
- exact-capable固定证据必须同时含完整档位下限和排除更高档的上界；
- Hard Gate必须覆盖明确pass、fail、unknown；
- 至少一个`failed_or_withdrawn`正例；
- 至少一个current/future混合反例；
- 至少一个“订单/backlog不等于收入或卡点”的反例。

### 4.2 盲法与不可变性

- internal与external ZIP必须由同一canonical目录生成并验证SHA-256完全相同；
- 盲包不得包含selection rationale、预期标签、裁决、agreement程序、历史结果、答案哈希或可反推答案的metadata；
- 两名真人提交后立即生成独立submission receipt，绑定包SHA、结果SHA、提交时间、annotator_id和schema校验结果；
- agreement只读取两份receipt绑定的原始结果；裁决写入单独文件，永不回写原始submission。

### 4.3 预裁决通过门槛

| 指标 | 门槛 |
| --- | ---: |
| Hard Gate一致率 | `>=0.95` |
| Final State一致率 | `>=0.90` |
| exact/exact样本对 | `>=10` |
| 0–4线性加权kappa | `>=0.70`，且只在exact/exact总体上计算 |
| Durable/Realized单边高风险升级 | `0` |

所有门必须同时通过。若失败，允许保存分歧与裁决，但不得运行或宣称“重新冻结所需的”HBM回归和Top-10复核已通过。

## 5. 冻结顺序

1. 完成v1.2合同机械校验；
2. 固定v0.4公开原文、Scope与任务，不写预期答案；
3. 生成并审计两个字节相同盲包；
4. 两名真人独立标注并生成正式receipt；
5. 计算预裁决agreement并判门；
6. 无论预裁决门是否通过，都将全部分歧写入单独裁决记录；若门失败，裁决不能追溯改变agreement；
7. 只有全部预裁决门通过后，才运行HBM 22条边界回归并做Top-10人工复核；
8. 产品负责人审阅全部证据后，另行签署`frozen`决定。

## 6. 当前证明边界

本文件只批准v1.2候选合同和v0.4验证设计。它不证明v1.2可实现、不证明标注一致性已改善、不证明任何公司是投资标的，也不授权修改运行代码。

## 7. v0.4实际执行结果（2026-08-16）

v0.4两份真人submission均已校验并由正式receipt封存。预裁决结果如下：

| 指标 | 实际值 | 判定 |
| --- | ---: | --- |
| Hard Gate一致率 | `23/27 = 85.19%` | fail |
| Final State一致率 | `4/5 = 80.00%` | fail |
| exact/exact样本对 | `11` | pass |
| 线性加权kappa | `1.00` | pass |
| Durable/Realized单边高风险升级 | `0` | pass |
| 总门 | — | **fail** |

17个标签分歧已全部裁决，原始submission和原始agreement保持不可变。裁决另发现三个P0：

- `TD-01`：要求最终Earnings状态的H04-C01只提供5/8个加权维度，完整Decision Coverage最高只有60%；
- `ED-01`：Mohawk Valley的`revenue production`制造阶段描述与“已确认产品收入”被混用；
- `TV-01`：validator只能校验schema，不能从Ordinal任务机械重算Coverage、Hard Gate和最终State。

因此v1.2继续保持`freeze_candidate`；HBM 22条回归和Top-10复核均未运行。下一轮必须先修复任务完备性、收入语义和语义validator，再使用全新未解盲案例执行v0.5，不得重复使用v0.4案例作为新Holdout。
