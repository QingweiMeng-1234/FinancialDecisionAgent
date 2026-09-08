# Theme Chokepoint Scoring v1.4 迁移与验证计划

## 1. 输入结论

v0.5预裁决Hard Gate一致率84.375%、Final State一致率60%，未通过重新冻结门。19项分歧已全部裁决，但裁决后结果不改变预裁决失败。

## 2. 本版只修复三项

| Finding | 修复 | 验收 |
| --- | --- | --- |
| CF-05-01 | 5%客户风险拆成2.5%集中暴露＋2.5%关系保护 | 权重仍合计100；集中比例可独立计分；合同缺失只影响保护维度 |
| CF-05-02 | Replacement强制`product/service`模式与profile绑定 | 缺mode或错profile时task validation失败 |
| AF-05-01 | Ordinal强制`bound_basis` | 虚假exact被拒绝；合法lower_bound保留未解析高档 |

## 3. TDD证据要求

1. 先新增exact无上界、Replacement缺mode、service错用product profile三个失败测试。
2. 确认失败原因是缺少目标行为。
3. 最小实现后运行focused suite。
4. 再运行v1.3与v1.4两套回归，证明旧工具未被改写。
5. 保存测试数量、退出码和静态语法检查结果。

## 4. v0.6生成要求

- 只使用全新公司/产品案例；
- 至少一个Earnings案例同时包含客户占比和合同保护原文，以验证拆分维度；
- 至少一个`service_replacement`和一个`product_replacement`；
- 每个Ordinal模板包含`bound_basis`；
- 至少16个设计上exact-capable条目，为10对真实exact/exact留余量；
- 固定官方原文、文件哈希、时间语义、Evidence Capability和Scope限制；
- 两个盲包ZIP必须字节一致。

## 5. 停止条件

若新案例无法提供模式匹配的关键分母、官方授权、可比性能或上界原文，必须把条目标为unknown-capable或更换案例；不得为了凑exact数量编写选择性限制或泄漏预期标签。

## 6. 执行结果与关闭决定

截至2026-08-16，本计划已完成：

- 全新v0.6双盲ZIP字节一致，原始submission与receipt均封存；
- 预裁决Hard Gate一致率100%、Final State一致率100%、exact/exact 14对、线性加权kappa 1.0、高风险单边升级0；
- 13项实质Ordinal分歧完成产品负责人裁决，5项State只做机械重算，裁决后v1.4语义校验通过；
- HBM/先进封装22/22项可执行边界回归通过；
- AI数据中心电力/冷却Top-10唯一公司复核通过：10家Accepted、1家Reject反例、0个missed candidate、12/12成对关系aligned；
- Durable、Realized/Scaled和Weak Earnings三个真实正例均保留在Golden中。

因此`theme-chokepoint-scoring-v1.4`转为`frozen`。冻结只授权其作为评分语义与实现基线，不等于完整Agent已实现、部署或形成投资建议。
