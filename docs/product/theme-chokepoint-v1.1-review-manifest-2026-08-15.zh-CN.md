# Theme Chokepoint v1.1 当前审查文件 Manifest

## 1. 审查入口

本Manifest固定2026-08-15独立审查裁决后的当前文件集合。v1.1已撤回冻结并恢复为设计候选。后续人工或模型审查必须先读取本文件，避免把历史v1、Golden v0.1的已修订语义或已撤回的冻结结论当成当前规范。

权威优先级：

```text
v1.1 scoring contract candidate
> independent-review adjudication
> HBM boundary regression
> Golden v0.2 revision layer
> Golden v0.1 inherited claims
> PRD explanatory text
> decision history and source audit
> historical v1 contract
```

同层冲突时停止发布并提交人工审查，不得自行选择更宽松解释。

## 2. 当前文件与哈希

| 文件 | 状态/角色 | SHA-256 |
| --- | --- | --- |
| `theme-chokepoint-scoring-contract-v1.1.zh-CN.md` | `freeze_candidate`；当前唯一候选语义，不是已批准实现基线 | `C19ECBA25F29FE016598FC78F28DD47CE3A45D0C8527FDED89AB0EEF8C0C1846` |
| `theme-chokepoint-v1.1-independent-review-adjudication-2026-08-15.zh-CN.md` | 新独立审查的逐项裁决与动作记录 | `43959359C23F8AD65EDB708F58FA5D89B92166F81B29DAF680C01AAE2ECF917D` |
| `hbm-advanced-packaging-golden-regression-v1.1-2026-08-15.zh-CN.md` | `boundary_pass`；15条边界断言，不是冻结证明 | `89FD14B8959F41DA1274A8089D865BF3E757580E56EF4416337C7C544905E47A` |
| `hbm-advanced-packaging-golden-claim-set-v0.2.zh-CN.md` | `versioned_boundary_input`；对v0.1的规范修订层 | `3BBB0C2EA3B83C9596C48E8D151211F38D486665FAA557BDA7858C09826652A8` |
| `hbm-advanced-packaging-golden-claim-set-v0.1.zh-CN.md` | 历史基础层；只继承未被v0.2覆盖的Evidence Card/Claim | `46F8FC3A89D73E1712BE09FB52F06C18CEE62F4460D1B66696D59F6BA42EE491` |
| `theme-chokepoint-research-agent-prd.md` | Draft v0.6；产品范围与分阶段需求 | `2A1F62CFA7C53C7881676002EB2EA63F73E02E637A5BF31E78099BFFBE584467` |
| `theme-chokepoint-scoring-v1.1-decision-baseline.zh-CN.md` | `accepted_decision_record_with_reopened_freeze_gate` | `C8C1F2202CA67BCF192F614C2EEB2161ACB91216146DE53AB5CAE643158C02CA` |
| `golden-case-source-audit-2026-08-15.md` | 原文来源、定位、哈希与证明边界 | `0EB69EA648EBF929E0C68E00CF1E0F0031E653550718C99FEE9276C3F60024A8` |
| `theme-chokepoint-scoring-contract-v1.zh-CN.md` | 历史合同；已被v1.1替代，不得用于新实现 | `B0C2483C4A1C307CD612447F156BB46E5758A09A99D35073AC189F64064239D0` |

Manifest自身不写入自身哈希；交付时由外部`Get-FileHash`记录。

## 3. 审查规则

1. 不得只审v1或Golden v0.1后声称是v1.1审查。
2. Golden必须按“v0.1基础层 + v0.2覆盖层”合并解释，`SEG-HBM-06`和单一maturity断言已经退役。
3. PRD中的评分摘要只用于产品说明；候选阈值、区间、Gate和状态优先级以v1.1候选合同为准，但不得称其已批准。
4. 回归报告只证明固定输入下的边界行为，不证明正向状态召回、跨主题排序、运行代码、部署或实时产业状态。
5. 任一规范文件哈希变化都使本Manifest和既有回归证明失效；必须说明是非语义勘误还是新合同版本，并按合同第17节处理。

## 4. 当前候选边界与冻结阻断项

- 已形成候选：证据区间、Claim复用、谓词级条件、证据家族去重、四套维度/权重/锚点、Coverage、Hard Gate、状态优先级、10卡预算、Materiality/Share/Displacement、Relief Horizon和趋势语义。
- 尚未冻结：线性权重、状态阈值、默认财务期间以及标注/状态/排序验收阈值。
- 尚未实现：运行时评分器、持久化Schema、检索/原文验证loop、监控调度和可执行Golden测试。
- 尚未由公开证据证明：本Golden中的严格公司排名、Samsung产品Materiality、Micron持续份额、SK hynix生产采用、Amkor对CoWoS的实际替代、任何`durable_chokepoint_owner`正例。
- 重新冻结必做：AI数据中心电力/冷却跨主题Golden、所缺真实正例、双标注盲评、分歧裁决、混淆矩阵、成对排序和Top-10校准。
