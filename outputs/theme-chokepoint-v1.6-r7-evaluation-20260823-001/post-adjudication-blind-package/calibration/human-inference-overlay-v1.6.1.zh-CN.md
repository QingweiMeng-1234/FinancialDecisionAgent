# Human Inference Overlay v1.6.1

本Overlay记录业务Owner对v1.6公开证据边界的裁决。它不把代理证据改称直接测量；所有例外必须标记为human_inferred。

## 允许的human_inferred边界

1. Demand proxy：当前Segment的量化年度产能扩张若由权威原文明示归因于当前强劲客户需求，可按相同百分比锚点形成human_inferred lower bound；不得形成exact 4。
2. Closed supplier universe：若权威原文证明目标Scope属于一个封闭产品族且该产品族只有有限供应商，可形成human_inferred concentration floor；缺少原子Scope有效份额与90天Failover时不得exact。
3. Functional scarcity：同时存在多年交付周期、量化供需缺口或预订至多年以后，并且没有公开短期缓解时，可形成human_inferred Concentration lower bound [2,4]；不得称为direct failover measurement。
4. Substitute absence：当前技术被证明为必要/唯一，且完成的不同路线搜索没有找到Ready/Production覆盖证据时，可形成human_inferred Substitute Weakness lower bound [1,4]；不得exact。
5. Credible alternative：不同路线已有真实订单、翻新或生产采用，但覆盖率未知时，必须形成upper_bound [0,2]，不能形成lower bound。

## 编码规则

- 原文事实经过百分比计算、阈值、公式或流程映射后统一标记rule_derived。
- direct_measurement仅用于原文直接给出最终评分所需完整组件且无需计算的情况。
- 只有floor=k且无独立ceiling时，[k,4]必须编码为lower_bound。
- human_inferred只有在业务Owner确认后才能进入正式Gold和机械State；未确认建议必须进入人工路由。
