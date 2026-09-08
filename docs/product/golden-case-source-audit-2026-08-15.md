# Golden Case 原文可得性审计

## 1. 审计结论

审计日期：2026-08-15  
评分合同：`theme-chokepoint-scoring-v1`  
审计对象：

1. `AI服务器 → HBM → 先进封装`
2. `AI数据中心 → 电力设备/冷却`

本轮结论是 **Golden Case 数据集选择结论，不是产业结论，也不是投资建议**。

建议：

- **主 Golden Case：`AI加速器 → HBM → 2.5D/先进封装`。** 它能够同时测试 Segment Chokepoint、在位者防御、挑战者替代阶段和利润传导，尤其适合检验“开发完成、送样、量产准备、商业出货、形成收入、持续取得份额”不能混为一谈。
- **第二验证集：`北美 AI 数据中心负荷 → 电网接入/高压设备 → 数据中心电力与冷却设备`。** 它对需求压力、长交期、积压订单、扩产和卡点迁移的原文覆盖更强，适合检验“卡点增强、减弱或转移”；但当前公开材料不足以稳定判断某一家设备商不可替代。
- 不建议把“AI服务器—HBM—先进封装”当成一个节点，也不建议把“AI数据中心电力设备”当成一个节点。正式标注时必须拆成可独立评分的产品/环节。

推荐的主 Golden Case 边界：

```text
theme_id: ai_accelerator_memory_and_packaging
product_anchor: NVIDIA B200-class AI accelerator
segment_nodes:
  - HBM3E/HBM4 stack
  - HBM assembly and test
  - 2.5D interposer/integration and advanced packaging
region: global
time_horizon_months: 6-24
as_of_date: 2026-08-15
```

推荐的第二验证集边界：

```text
theme_id: north_america_ai_data_center_power
product_anchor: hyperscale/AI data-center load additions
segment_nodes:
  - interconnection and available power
  - high-voltage transformer/switchgear
  - data-center power distribution and UPS
  - thermal management/liquid cooling
region: North America first; other regions separate
time_horizon_months: 6-24
as_of_date: 2026-08-15
```

## 2. 审计方法与证据边界

本轮只回答一个问题：**能否用可追溯原文构造一个足以暴露评分合同错误的 Golden Case？**

使用以下原文层级：

- 下游产品规格、系统运营方和监管/实验室材料：用于需求、关键性、并网和交付约束；
- 供应商财报、电话会和产能材料：用于当前产出、扩产、量产阶段和财务传导；
- 挑战者材料：用于样品、认证、设计导入和替代阶段，但不单独证明客户已完成替换；
- 搜索结果摘要只用于发现来源，不作为最终评分证据。

状态说明：

| 状态 | 含义 |
| --- | --- |
| `Gold-ready` | 已找到可直接进入 Evidence Card 的官方原文，且能定位到页面、段落或 PDF 页码 |
| `Amber` | 已有官方原文，但缺客户侧交叉验证、当前份额、认证范围或统一产品口径 |
| `Red` | 当前公开材料不足，不能支持该判断 |

本报告没有计算四套业务分数。来源可得性不等于业务评分，`Gold-ready` 也不表示该维度应得高分。

## 3. 候选一：AI 加速器—HBM—先进封装

### 3.1 原文链条

1. **下游规格证明 HBM 是系统内明确、可量化的关键输入。** NVIDIA 的 DGX B200 规格写明：`GPU Memory 1,440 GB total, 64 TB/s HBM3e bandwidth`。这能支持 HBM 用量与带宽要求，不能证明某家 HBM 供应商不可替代。
2. **供应商原文能证明产品和产能阶段。** SK hynix 在 2025-09-12 表述为完成 HBM4 开发并“ready to supply ... according to customer plans”；Samsung 在 2026-02-12 表述为已开始 HBM4 量产并“shipped commercial products to customers”。两者处于不同证据强度，均仍缺客户名称、认证范围和持续份额。
3. **财报原文能证明收入与利润传导。** Micron FY2026 Q3 prepared remarks 披露已实现超过 10 亿美元 HBM4 收入，并给出 DRAM 收入、价格、毛利率和先进封装扩产时间表。这比“产品发布”更接近 `material_revenue`，但不自动证明 `sustained_share_gain`。
4. **先进封装存在扩产和替代路线。** TSMC 2025 Q1 电话会称，基于客户强劲需求，公司努力在 2025 年将 CoWoS 产能翻倍。Amkor 原文则显示 2.5D 产能已认证并扩充、与 TSMC 合作支持美国先进封装双认证，同时仍存在“评估中”“继续认证”“扩产计划”等较早阶段。
5. **反证已经存在。** Samsung 商业出货、Micron HBM4收入、Amkor 2.5D 与双认证路径，都反对“当前龙头天然不可替代”的简单结论。

### 3.2 Claim Coverage

| 评分对象/维度 | 状态 | 可用原文 | 仍缺什么 |
| --- | --- | --- | --- |
| Segment / Demand Pressure | `Gold-ready` | NVIDIA B200 HBM3E 规格；Micron 对 AI 内存需求和数据中心 DRAM/NAND 的量化说明 | 不同加速器世代的单位 HBM 含量时间序列 |
| Segment / Downstream Criticality | `Gold-ready` | NVIDIA 明确把 HBM3E 容量和带宽写入系统规格；Micron称 AI 系统性能在架构上依赖内存子系统性能与容量 | 缺货导致具体客户延迟出货的客户侧事件原文 |
| Segment / Effective Supply Concentration | `Amber` | SK hynix、Samsung、Micron均有当前产品/量产原文；TSMC、Amkor有先进封装产能原文 | 合格可售产出、客户批准供应商清单、真实可用余量 |
| Segment / Qualification Barrier | `Amber` | Samsung“商业出货”、Amkor“qualified installed base / dual qualification”提供阶段信号 | HBM客户认证周期、失败记录、重新认证成本 |
| Segment / Capacity Inelasticity | `Gold-ready` | Micron列出绿地厂、设备、工人、许可、能源与良率约束；TSMC披露CoWoS翻倍扩产；Amkor披露产能爬坡 | 跨供应商统一口径的可售产出和良率 |
| Segment / Substitute Weakness | `Amber` | Samsung、Micron和Amkor构成明确反证路线 | 替代路线在同一目标GPU/ASIC上的认证、成本与产能对比 |
| Segment / Constraint Persistence | `Amber` | Micron称 DRAM/NAND 紧张预计持续至 2027 年以后，并给出新产能时间表 | HBM与非HBM不能混用；需要HBM专属的供需与扩产口径 |
| Company Defensibility / Customer Sourcing | `Red` | 供应商均称满足客户计划或已商业出货 | 客户身份、单供/双供、采购份额、成功切换、价格让步 |
| Replacement / Maturity | `Gold-ready` | SK hynix“量产准备”；Samsung“量产+商业出货”；Micron“>10亿美元HBM4收入”；Amkor“认证+扩产” | 持续份额增长、同一客户内的替代前后对照 |
| Earnings Transmission | `Gold-ready` | Micron有HBM4收入、DRAM收入、价格、毛利和封装产能；Amkor有2.5D收入/产能与利润约束 | TSMC CoWoS独立收入与利润；SK hynix/Samsung产品级利润 |

### 3.3 五类 Golden 标签覆盖

| Golden 标签 | 可构造性 | 建议样本 | 审计边界 |
| --- | --- | --- | --- |
| durable owner | 暂不能直接定 Gold | TSMC CoWoS 或在位 HBM 供应商作为待证对象 | 客户侧锁定、切换成本和批准供应商原文不足，不能先写“不可替代” |
| contested owner | 强 | SK hynix 与 Samsung/Micron 的 HBM4 进度对照 | 必须绑定同一产品世代、客户范围和时间窗口 |
| 高份额但防御证据不足的龙头 | 强 | 当前份额领先者作为 false-leader 测试样本 | 市占率不能替代客户采购、认证和有效产能证据 |
| 技术/送样不错但未 replacement-ready 的挑战者 | 强 | Amkor 新2.5D/双认证路线，或尚处客户计划/认证阶段的产品 | “评估、送样、量产准备”不得升级为规模替代 |
| 真卡点但 weak earnings capture | 中强 | CoWoS/先进封装对 TSMC 的独立财务暴露，或2.5D对多业务OSAT的暴露 | 卡点成立不代表产品级收入占比、利润增量和资本回报已被证明 |

这套样本最大的价值不是证明谁最强，而是可以构造一条清晰的阶段梯度：

```text
客户需求/规格
→ 开发完成
→ 量产准备
→ 商业出货
→ material revenue
→ sustained share gain（当前仍缺）
```

## 4. 候选二：AI 数据中心—电力设备/冷却

### 4.1 原文链条

1. **需求侧非常强。** DOE/LBNL称美国数据中心用电占比从2023年的约4.4%可能升至2028年的6.7%–12%，总用电由176 TWh升至325–580 TWh。PJM 2025初步预测明确把数据中心列为负荷调整项，并将长期夏季负荷年化增速由1.6%上调至2.0%。
2. **电网与设备约束有一手材料。** NERC 2025报告称数据中心等大负荷改变了需求增长方式，高压变压器持续面临长交期，数据中心的接入速度可能快于配套输电和发电建设。
3. **公司财务传导材料非常强。** Eaton 2025 Q2材料披露数据中心订单约增55%、收入约增50%，Electrical Americas backlog同比增17%；Vertiv 2025 Q4披露订单、book-to-bill、backlog、销售额、利润率和现金流。
4. **扩产也有直接反证。** GE Vernova 2025-01-29宣布未来两年近6亿美元美国工厂投资，其中电网设备工厂计划扩充开关设备、电容器和仪表变压器产能。这证明供应正在响应，不能把当前长交期直接外推为永久卡点。
5. **卡点会转移。** NERC材料同时指向可用电力、并网、输电、发电、变压器、许可和负荷建模。设备订单强并不能证明“设备商”是最终卡点，卡点可能转移到电源或并网许可。

### 4.2 Claim Coverage

| 评分对象/维度 | 状态 | 可用原文 | 仍缺什么 |
| --- | --- | --- | --- |
| Segment / Demand Pressure | `Gold-ready` | DOE/LBNL用电预测；PJM负荷预测；NERC大型负荷说明 | 项目重复申报、取消、推迟和已签供电合同的清洗 |
| Segment / Downstream Criticality | `Gold-ready` | NERC称大负荷可快于输电/发电建设、数据中心持续运行且对电能质量容忍度低 | 某类设备短缺导致具体数据中心投产延迟的项目级原文 |
| Segment / Effective Supply Concentration | `Red` | Eaton、Vertiv、GE Vernova均证明存在多家供应与扩产 | 按UPS、开关设备、大型变压器、冷却等产品拆分后的合格供应商份额 |
| Segment / Qualification Barrier | `Amber` | NERC提供系统建模、接入和设备约束背景 | 客户批准厂商清单、认证周期、现场替换和保修影响 |
| Segment / Capacity Inelasticity | `Gold-ready` | NERC称高压变压器持续长交期；公司扩产需要新增资产与工厂 | 统一产品规格下的当前交期、可售产能和利用率 |
| Segment / Substitute Weakness | `Amber` | GE Vernova扩产是供应响应反证；NERC提到储能、备用电源和需求侧灵活性 | 替代技术在同一负荷、可靠性和时间窗口内的TCO与可用规模 |
| Segment / Constraint Persistence | `Gold-ready` | NERC把供应链、并网、输电、发电和许可列为中长期问题 | 各区域和设备品类不能混为同一持续性 |
| Company Defensibility / Customer Sourcing | `Red` | 订单和backlog只能证明需求与收入可见性 | 客户为何不能换供应商、双供情况、标准接口、服务网络锁定 |
| Replacement / Maturity | `Red/Amber` | GE Vernova扩产、新产线与Vertiv/Eaton产品布局提供早期信号 | 挑战者认证、设计导入、商业部署、份额变化的连续原文 |
| Earnings Transmission | `Gold-ready` | Eaton与Vertiv有订单、销售、backlog、利润率与现金流 | 产品级收入拆分、价格/成本传导、资本强度和取消条款 |

### 4.3 五类 Golden 标签覆盖

| Golden 标签 | 可构造性 | 问题 |
| --- | --- | --- |
| durable owner | 弱 | 公开资料更容易证明订单强，难证明某家公司无法被替换 |
| contested owner | 中 | 可比较Eaton、Vertiv、GE Vernova等，但产品口径和客户重叠度不统一 |
| 高份额但防御证据不足的龙头 | 强 | 很适合测试“backlog/龙头地位不等于不可替代” |
| 技术/送样不错但未 replacement-ready 的挑战者 | 弱至中 | 公司多为综合设备商，公开的客户认证阶段不连续 |
| 真卡点但 weak earnings capture | 强 | 并网、电源和高压变压器可是真卡点，但未必能映射到单一上市公司利润 |

因此，这个候选更适合验证 Segment 与监控逻辑，不如 HBM 候选适合验证完整的公司竞争状态机。

## 5. 两个候选的 Source-Audit 对照

| 审计问题 | HBM/先进封装 | 电力设备/冷却 |
| --- | --- | --- |
| 需求侧原文 | 强：GPU系统规格和内存需求 | 很强：DOE/LBNL、PJM、NERC |
| 供应侧原文 | 很强：产品、量产、收入、扩产 | 很强：订单、backlog、扩产、长交期 |
| 客户认证/采购行为 | 弱，且这是最关键缺口 | 更弱，采购分散且通常不披露 |
| 挑战者成熟度梯度 | 强：准备量产、商业出货、material revenue可区分 | 弱至中：多为扩产或产品布局，认证阶段不连续 |
| 公司利润传导 | 强：Micron；其他公司需补产品级拆分 | 很强：Eaton、Vertiv |
| 构造反证 | 强：多家HBM供应商和OSAT/双认证路线 | 强：多家设备商扩产、储能/备用电源/需求响应 |
| 观察卡点迁移 | 中：HBM stack、封装、良率、晶圆之间迁移 | 很强：电源、并网、输电、变压器、UPS、冷却之间迁移 |
| 五类 Golden 标签完整度 | 较高 | 中等 |
| 适合作为 | 主 Golden Case | 第二验证集 |

## 6. 不应进入 Gold 的证据

以下材料可以保留为发现线索，但不得直接进入评分：

1. **“变压器平均交期120周、部分80–210周”。** 本轮找到的 DOE 托管附件和网页多为二次引用或材料附件，尚未稳定追溯到同口径的原始统计、产品定义、地区与观察日期。可用 NERC 的“persistently faced long lead times”证明长交期存在，但不能把120周写成已验证精确值。
2. **供应商自称“行业第一”“领导者”“满足客户需求”。** 只能证明公司陈述和产品阶段，不能证明客户单供、无替代或持续份额。
3. **搜索结果摘要。** 只用于定位原始PDF或官方页面，不作为 Evidence Card 的 `exact_quote`。
4. **总公司订单或backlog。** 未拆到目标产品和地区前，不能直接证明某一细分设备是卡点，也不能证明公司不可替代。
5. **产能宣布。** 必须继续追踪 `funded → under construction → equipment installed → qualification → yield ramp → saleable output`。

## 7. 正式 Golden Set 的最小标注计划

### 7.1 主 Golden Case

先制作 20–30 个 Claim，而不是直接给公司总分：

- 5–7个 Segment Claim：需求、关键性、有效供给集中、认证、扩产、替代、持续性；
- 6–8个 Company Defensibility Claim：按公司、产品、客户范围分别建立；
- 5–7个 Replacement Claim：每个产品只允许一个明确成熟度阶段；
- 4–6个 Earnings Claim：收入暴露、量价、毛利、资本强度、确认时间。

必须单独建立以下反证 Claim：

- Samsung/Micron 的商业交付是否已经形成同一客户、同一GPU世代的有效替代；
- Amkor 2.5D/双认证是否达到规模可售产出，而不只是认证或投资；
- CoWoS 扩产是否使卡点从封装转移到 HBM、晶圆、良率或其他环节；
- 高份额供应商是否存在客户双供、份额下降或价格让步。

### 7.2 第二验证集

必须拆成四个子集分别评分：

1. available power/interconnection；
2. high-voltage transformer/switchgear；
3. data-center power distribution/UPS；
4. thermal management/liquid cooling。

该验证集的重点不是寻找一个“电力设备龙头”，而是验证：

```text
需求上升
→ 卡点落在某个具体节点
→ 供应商扩产/替代技术响应
→ 卡点减弱或迁移
→ 公司订单和利润是否随之变化
```

## 8. 正式来源索引

### 8.1 HBM 与先进封装

| ID | 来源 | 日期 | 可支持字段 | 限制 |
| --- | --- | --- | --- | --- |
| HBM-01 | [NVIDIA DGX B200 Specifications](https://www.nvidia.com/en-us/data-center/dgx-b200/) | retrieved 2026-08-15 | HBM3E容量、带宽、系统用量、downstream criticality | 不披露HBM供应商和采购份额 |
| HBM-02 | [Micron FY2026 Q3 Prepared Remarks](https://s25.q4cdn.com/621799436/files/doc_financials/2026/q3/Q3-FY26-Prepared-Remarks.pdf) | 2026-06-24 | HBM4收入、供需、SCA、扩产、DRAM收入/价格/毛利 | Micron自述；部分数据是DRAM/NAND整体而非HBM独立口径 |
| HBM-03 | [SK hynix HBM4 development and mass-production readiness](https://news.skhynix.com/en/sk-hynix-completes-worlds-first-hbm4-development-and-readies-mass-production/) | 2025-09-12 | 开发完成、量产准备、性能、工艺 | 不能单独证明客户认证和规模采购 |
| HBM-04 | [Samsung commercial HBM4 shipment](https://news.samsung.com/global/samsung-ships-industry-first-commercial-hbm4-with-ultimate-performance-for-ai-computing) | 2026-02-12 | 量产、商业出货、性能、扩产、下一代送样 | 客户未具名；持续份额未证明 |
| HBM-05 | [TSMC 2025 Q1 Transcript](https://investor.tsmc.com/english/encrypt/files/encrypt_file/reports/2025-04/7630274eecc1197a4e3ea6a415f44a47204fe10a/TSMC%201Q25%20Transcript.pdf) | 2025-04-17 | 客户需求、2025年CoWoS扩产 | CoWoS客户采购和替代路线未披露；正式标注需补页码/内容哈希 |
| HBM-06 | [TSMC 2025 Annual Report](https://investor.tsmc.com/static/annualReports/2025/english/index.html) | 2026 | 公司、风险、财务和先进封装背景 | 不能替代产品级CoWoS产能/收入数据 |
| HBM-07 | [Amkor 2024 Q3 Earnings Call](https://ir.amkor.com/static-files/df28b7c7-6b92-4e32-93df-161267cde80a) | 2024-10-28 | 2.5D认证/利用率/扩产、TSMC双认证路线、HBM约束、利润影响 | 第三方转录文本由Amkor IR托管；当前产能状态可能已变化，必须刷新2025–2026进度 |

### 8.2 AI 数据中心电力与冷却

| ID | 来源 | 日期 | 可支持字段 | 限制 |
| --- | --- | --- | --- | --- |
| PWR-01 | [DOE: Data Center Electricity Demand](https://www.energy.gov/articles/doe-releases-new-report-evaluating-increase-electricity-demand-data-centers) | 2024-12-20 | 美国数据中心用电占比、TWh和2028预测 | 国家级预测，不直接证明某设备品类订单 |
| PWR-02 | [LBNL 2024 U.S. Data Center Energy Usage Report](https://eta-publications.lbl.gov/sites/default/files/2024-12/lbnl-2024-united-states-data-center-energy-usage-report.pdf) | 2024-12 | DOE数字的底层研究、需求情景 | 预测区间宽；不等于已签约负荷 |
| PWR-03 | [PJM 2025 Preliminary Load Forecast](https://www.pjm.com/-/media/DotCom/committees-groups/committees/pc/2025/20250107/item-07-2025-preliminary-pjm-load-forecast.pdf) | 2025-01-07 | 数据中心负荷调整、夏冬峰值和增速 | 初步预测；区域限定在PJM |
| PWR-04 | [NERC 2025 ERO Reliability Risk Priorities Report](https://www.nerc.com/globalassets/our-work/reports/ero-reliability-risk-priorities-report/2025_risc_ero_priorities_report.pdf) | Board accepted 2025-08-14 | 大负荷、并网、电源/输电建设、高压变压器长交期、卡点迁移 | 不给具体供应商和统一交期数值 |
| PWR-05 | [Eaton 2025 Q2 Analyst Presentation](https://www.eaton.com/content/dam/eaton/company/investor-relations/quarterly-earnings/filings/2025/q2/2Q-2025-analyst-presentation.pdf) | 2025-08-05 | 数据中心订单/收入、Electrical Americas销售/backlog、利润率 | 不能证明客户不能换供应商 |
| PWR-06 | [Vertiv 2025 Q4 Results](https://investors.vertiv.com/news/news-details/2026/Vertiv-Reports-Strong-Fourth-Quarter-with-Organic-Orders-Growth-of-252-and-Diluted-EPS-Growth-of-200-Adjusted-Diluted-EPS-37/default.aspx) | 2026-02-11 | 订单、book-to-bill、backlog、销售、利润率、现金流 | 公司级数据；产品和客户结构仍需拆分 |
| PWR-07 | [GE Vernova U.S. Manufacturing Investment](https://www.gevernova.com/news/press-releases/ge-vernova-invest-almost-600-million-us-factories-facilities-over-next-two-years) | 2025-01-29 | 电网设备扩产、投资、时间表、供应响应反证 | 计划不等于稳定可售产出；品类不覆盖所有大型变压器 |

## 9. 已冻结 PDF 快照

以下哈希仅用于本轮审计复现；正式 Golden Set 仍应把原文快照放入受版本管理的 Evidence Store，而不是引用临时文件：

| Source ID | SHA-256 |
| --- | --- |
| HBM-02 | `A3CE62B84A059E35FAE80C2BFD5C89F9AF334193FD6ACEFF698FC3008E7D4C27` |
| HBM-07 | `D7F63B111260A5ECC84267A83495501E277D8B05E989AB7AE76397FB9B0E180E` |
| PWR-03 | `8A74E2E447FADF14370F6AF09FA97340F9FAA3A583B1FCA777BB608FD8B74B0B` |
| PWR-04 | `C34C38C41A79BBDC436A7DFA9A58A8E6778AAE7BEE3D1F9AE1514238314AD0FA` |
| PWR-05 | `E5BDDDDC57F8A0CCA057FD1B11C0188A8E5D2F6704BEEBAE1D33519AC911113D` |

NERC 下载响应在 PDF header 前包含 45 字节 HTML 包装；表中 PWR-04 是去除包装、从 `%PDF-` 起保存后的规范化 PDF 哈希。正式 Evidence Store 需要同时保存原始响应哈希、规范化内容哈希和转换记录。

## 10. Stage 0 的下一道门

Source Audit 已足以选择主次案例，但 **尚不足以冻结 Golden 标签答案**。进入人工标注前还需要：

1. 为 HBM/先进封装补至少两份客户侧认证、双供、切换或采购原文；若公开资料不存在，必须把 `customer_sourcing_evidence=unknown` 固定为预期答案，而不是用供应商材料填补。
2. 刷新 Amkor/ASE 2025–2026 的2.5D产能、认证和material revenue状态。
3. 将 HBM stack、HBM assembly/test 和2.5D integration拆成独立 Segment，并为每个Segment选择一个在位者、一个挑战者和一个反证。
4. 将电力验证集按设备品类和区域拆分，禁止把 Eaton/Vertiv/GE Vernova 的公司级backlog直接映射到“大型变压器”或“液冷”。
5. 人工标注员先独立给出 `supported/conflicted/unknown` 与评分区间，再运行Agent；Agent输出不得反向污染预期答案。

完成上述标注后，才能判断 `theme-chokepoint-scoring-v1` 是否能正确拒绝“龙头=不可替代”“公告=替代完成”“卡点=利润一定受益”这三类错误。
