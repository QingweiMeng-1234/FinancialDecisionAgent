# AI 加速器—HBM—先进封装 Golden Claim Set v0.1

## 1. 文档状态

| 字段 | 值 |
| --- | --- |
| 文档类型 | 人工 Golden Case Claim/Evidence Card 底稿 |
| 对应评分合同 | `theme-chokepoint-scoring-v1` |
| 版本 | `golden-claim-set-hbm-advanced-packaging-v0.1` |
| 截止日期 | 2026-08-15 |
| 研究链条 | `AI accelerator -> HBM -> HBM assembly/test -> 2.5D integration/advanced packaging` |
| 地区 | Global |
| 默认研究期限 | 12个月 |
| 当前状态 | Claim 边界已冻结；公司总分和最终 Golden 标签未冻结 |

本文件先冻结“原文能证明什么、不能证明什么”。它不是投资建议，也不比较股票预期收益。当前公开材料不足以把任何样本直接标为 `durable_chokepoint_owner`。

## 2. 标注原则

1. 搜索摘要、向量 chunk 和模型转述只能发现来源，不能进入最终证据。
2. `supported` 只表示原文足以支持本 Claim，不表示相关公司的完整评分已经成立。
3. 缺客户身份、双供、认证范围、采购份额或切换行为时，`customer_sourcing_evidence` 必须为 `unknown`。
4. 供应商自述可以证明其公开陈述的产品、产能、收入或计划阶段，不能单独证明客户完成认证、客户单供或持续份额增长。
5. 替代成熟度只使用合同枚举：

```text
concept -> prototype -> customer_sample -> customer_validation -> design_win
-> qualification_complete -> pilot_volume -> mass_production
-> material_revenue -> sustained_share_gain
```

6. “量产准备”不是 `mass_production`；“final qualification”不是 `qualification_complete`；“计划 high volume”不是已经 high volume。
7. HBM 供应商之间的竞争与先进封装路线之间的竞争分别评分。不得因为两者都服务 AI 加速器，就推断 Amkor HDFO 能直接替代 TSMC CoWoS，或某家 HBM 能直接替代另一家在某一 GPU/ASIC 上的认证产品。
8. Claim 状态由人工标注员独立给出；Agent 输出不得反向污染预期答案。

## 3. 范围拆分

本 Golden Case 至少包含三个独立 `segment_id`：

| segment_id | 评分对象 | 不得混入 |
| --- | --- | --- |
| `seg_hbm_stack` | HBM stack、带宽、容量、HBM 产品量产和收入 | 2.5D 集成产能、通用 DRAM 总产能 |
| `seg_hbm_assembly_test` | HBM 封装、堆叠、测试、良率和可售产出 | Foundry 前端晶圆、CoWoS 全部环节 |
| `seg_2_5d_integration` | Interposer/bridge、2.5D 集成、HDFO/CoWoS 等目标封装路线 | 未证明可互换的普通先进封装收入 |

公司 Claim 必须同时绑定 `company_id + product_id + segment_id + region + time_horizon + as_of_date`。本文件中的公司名只是候选评估对象，不代表已确定在位者或挑战者身份。

## 4. 来源清单

| source_id | 来源 | publication_date | retrieved_at | 版本标识 | 说明 |
| --- | --- | --- | --- | --- | --- |
| `HBM-01` | [NVIDIA DGX B200 Specifications](https://www.nvidia.com/en-us/data-center/dgx-b200/) | 页面未标日期 | 2026-08-15 | DOM SHA-256 `ec861dddb32e2d16deaca5bfdc4e1a27066e95c79d26f67efbc0a01606fe8b` | 下游系统规格；不披露 HBM 供应商 |
| `HBM-02` | [Micron FY2026 Q3 Prepared Remarks](https://s25.q4cdn.com/621799436/files/doc_financials/2026/q3/Q3-FY26-Prepared-Remarks.pdf) | 2026-06-24 | 2026-08-15 | PDF SHA-256 `A3CE62B84A059E35FAE80C2BFD5C89F9AF334193FD6ACEFF698FC3008E7D4C27` | 公司官方财报材料；部分口径为 DRAM/NAND 整体 |
| `HBM-03` | [SK hynix HBM4 development and mass-production readiness](https://news.skhynix.com/en/sk-hynix-completes-worlds-first-hbm4-development-and-readies-mass-production/) | 2025-09-12 | 2026-08-15 | DOM SHA-256 `fa924eabf3ac0fea32473dd9d8140354ac1f74dc15b94c72b317c9b30ad6b64c` | 供应商官方发布；证明量产准备，不证明客户采用 |
| `HBM-04` | [Samsung commercial HBM4 shipment](https://news.samsung.com/global/samsung-ships-industry-first-commercial-hbm4-with-ultimate-performance-for-ai-computing) | 2026-02-12 | 2026-08-15 | DOM SHA-256 `63928a277b355f1b81a306f49c7245830b31a4433821d1a062cbc06f60ae1879` | 供应商官方发布；客户未具名 |
| `HBM-05` | [TSMC 2025 Q1 Transcript](https://investor.tsmc.com/english/encrypt/files/encrypt_file/reports/2025-04/7630274eecc1197a4e3ea6a415f44a47204fe10a/TSMC%201Q25%20Transcript.pdf) | 2025-04-17 | 2026-08-15 | `pending_snapshot` | 已定位扩产原文，但本轮未取得可复现 PDF 哈希和页码，不进入关键评分 |
| `HBM-08` | [Amkor Q4 2025 Earnings Call](https://ir.amkor.com/static-files/41b4afab-87b2-4633-b55e-8ddd7a8e144f) | 2026-02-09 | 2026-08-15 | PDF SHA-256 `58CB2C3CB514DFF002BC8743EFD40225F31DC66FEC2D93BC2D5B76C6CA1B6D77` | Amkor IR 托管的 LSEG transcript；需保留转录误差限制 |

`HBM-05` 只作为待补来源。其已定位原文为：

> Based on our customers' strong demand, we are also working hard to double our CoWoS capacity in 2025 to support their needs.

在补齐 PDF 内容哈希、页码和可复现快照前，相关 Claim 不得从 `unknown` 升级为 `supported`。

## 5. Evidence Cards

以下卡片保存进入 Claim 的最小原文。相同来源的不同原文拆为不同 `evidence_id`，避免一个长文档被当作单一万能证据。

### `EV-HBM-01`

- `claim_id`: `SEG-HBM-01`
- `source_id`: `HBM-01`
- `content_sha256`: `ec861dddb32e2d16deaca5bfdc4e1a27066e95c79d26f67efbc0a01606fe8b`
- `exact_quote`: “GPU Memory 1,440 GB total, 64 TB/s HBM3e bandwidth”
- `page/section/text_offset`: Web page, “NVIDIA DGX B200 Specifications” table, “GPU Memory” row
- `publisher`: NVIDIA
- `source_type`: Official product specification
- `publication_date`: Unknown
- `data_as_of`: Page state retrieved 2026-08-15
- `retrieved_at`: 2026-08-15
- `stance`: `supports`
- `limitations`: 只证明系统使用 HBM3e 的容量和带宽；不披露 HBM 供应商、采购份额、短缺或利润。

### `EV-HBM-02`

- `claim_id`: `SEG-HBM-02`
- `source_id`: `HBM-02`
- `content_sha256`: `A3CE62B84A059E35FAE80C2BFD5C89F9AF334193FD6ACEFF698FC3008E7D4C27`
- `exact_quote`: “AI system performance is architecturally dependent on memory subsystem performance and capacity.”
- `page/section/text_offset`: PDF p.2, “Industry trends”
- `publisher`: Micron Technology
- `source_type`: Official earnings prepared remarks
- `publication_date`: 2026-06-24
- `data_as_of`: FY2026 Q3
- `retrieved_at`: 2026-08-15
- `stance`: `supports`
- `limitations`: 供应商陈述支持技术关键性，但不是客户故障或延迟出货事件。

### `EV-HBM-03`

- `claim_id`: `SEG-HBM-03`, `SEG-HBM-04`
- `source_id`: `HBM-02`
- `content_sha256`: `A3CE62B84A059E35FAE80C2BFD5C89F9AF334193FD6ACEFF698FC3008E7D4C27`
- `exact_quote`: “HBM’s growth and increasing trade ratio with every new generation further pressures non-HBM supply.”
- `page/section/text_offset`: PDF p.2, “Industry trends”
- `publisher`: Micron Technology
- `source_type`: Official earnings prepared remarks
- `publication_date`: 2026-06-24
- `data_as_of`: FY2026 Q3
- `retrieved_at`: 2026-08-15
- `stance`: `supports`
- `limitations`: 证明供应权衡方向，不给出 HBM 专属 wafer/bit 数量、跨供应商统一口径或客户批准产能。

### `EV-HBM-04`

- `claim_id`: `SEG-HBM-04`
- `source_id`: `HBM-02`
- `content_sha256`: `A3CE62B84A059E35FAE80C2BFD5C89F9AF334193FD6ACEFF698FC3008E7D4C27`
- `exact_quote`: “These greenfield projects are large, complex and time consuming. Further, the pace is constrained by several factors, including long lead time for fab construction across the world, shortage of workers with critical trade skills, complex regulations including permitting, and the need for enhanced energy infrastructure.”
- `page/section/text_offset`: PDF p.2, “Industry trends”
- `publisher`: Micron Technology
- `source_type`: Official earnings prepared remarks
- `publication_date`: 2026-06-24
- `data_as_of`: FY2026 Q3
- `retrieved_at`: 2026-08-15
- `stance`: `supports`
- `limitations`: 公司层面的结构性约束描述；不能直接给出某一 HBM 产品的精确扩产月数或可售余量。

### `EV-HBM-05`

- `claim_id`: `DEF-MU-01`, `REP-MU-01`, `EARN-MU-01`
- `source_id`: `HBM-02`
- `content_sha256`: `A3CE62B84A059E35FAE80C2BFD5C89F9AF334193FD6ACEFF698FC3008E7D4C27`
- `exact_quote`: “HBM4 12-high volume ramp is tracking twice as fast as HBM3E 12-high, and we have already shipped over $1 billion in HBM4 revenue.”
- `page/section/text_offset`: PDF p.4, “Technology and product leadership”
- `publisher`: Micron Technology
- `source_type`: Official earnings prepared remarks
- `publication_date`: 2026-06-24
- `data_as_of`: FY2026 Q3
- `retrieved_at`: 2026-08-15
- `stance`: `supports`
- `limitations`: 足以支持 `material_revenue`；不披露具名客户、同一 GPU 世代替代关系或持续份额增长。

### `EV-HBM-06`

- `claim_id`: `DEF-MU-02`
- `source_id`: `HBM-02`
- `content_sha256`: `A3CE62B84A059E35FAE80C2BFD5C89F9AF334193FD6ACEFF698FC3008E7D4C27`
- `exact_quote`: “The 16 signed agreements represent roughly 20% of our DRAM volume and a third of our NAND volume over this period.”
- `page/section/text_offset`: PDF p.3, “Business model transformation and strategic customer agreements”
- `publisher`: Micron Technology
- `source_type`: Official earnings prepared remarks
- `publication_date`: 2026-06-24
- `data_as_of`: FY2026 Q3; agreements generally cover calendar 2026–2030
- `retrieved_at`: 2026-08-15
- `stance`: `context_only`
- `limitations`: SCA 跨 DRAM、HBM（as appropriate）和 NAND，不能把 20% DRAM volume 解释为 HBM 单供或 HBM 客户锁定。

### `EV-HBM-07`

- `claim_id`: `DEF-SKH-01`, `REP-SKH-01`
- `source_id`: `HBM-03`
- `content_sha256`: `fa924eabf3ac0fea32473dd9d8140354ac1f74dc15b94c72b317c9b30ad6b64c`
- `exact_quote`: “The company is ready to supply the best-in-class HBM4 to customers according to their plans and to maintain competitive position”
- `page/section/text_offset`: Web article, key-message bullet under title
- `publisher`: SK hynix
- `source_type`: Official company news release
- `publication_date`: 2025-09-12
- `data_as_of`: 2025-09-12
- `retrieved_at`: 2026-08-15
- `stance`: `supports`
- `limitations`: 原文是 readiness 和供应商自述；没有具名客户、客户认证、商业出货、收入或份额证据。

### `EV-HBM-08`

- `claim_id`: `DEF-SKH-01`, `REP-SKH-01`
- `source_id`: `HBM-03`
- `content_sha256`: `fa924eabf3ac0fea32473dd9d8140354ac1f74dc15b94c72b317c9b30ad6b64c`
- `exact_quote`: “announced today that it has completed development and finished preparation of HBM4*, a next generation memory product for ultra-high performance AI, mass production for the world’s first time.”
- `page/section/text_offset`: Web article, opening paragraph
- `publisher`: SK hynix
- `source_type`: Official company news release
- `publication_date`: 2025-09-12
- `data_as_of`: 2025-09-12
- `retrieved_at`: 2026-08-15
- `stance`: `supports`
- `limitations`: 证明开发完成和量产准备，不等于合同枚举中的 `mass_production`。

### `EV-HBM-09`

- `claim_id`: `DEF-SAM-01`, `REP-SAM-01`
- `source_id`: `HBM-04`
- `content_sha256`: `63928a277b355f1b81a306f49c7245830b31a4433821d1a062cbc06f60ae1879`
- `exact_quote`: “it has begun mass production of its industry-leading HBM4 and has shipped commercial products to customers.”
- `page/section/text_offset`: Web article, opening paragraph
- `publisher`: Samsung Electronics
- `source_type`: Official company news release
- `publication_date`: 2026-02-12
- `data_as_of`: 2026-02-12
- `retrieved_at`: 2026-08-15
- `stance`: `supports`
- `limitations`: 足以支持 `mass_production`；客户未具名，未披露收入、采购份额和采用持续性。

### `EV-HBM-10`

- `claim_id`: `DEF-SAM-01`, `SEG-HBM-06`
- `source_id`: `HBM-04`
- `content_sha256`: `63928a277b355f1b81a306f49c7245830b31a4433821d1a062cbc06f60ae1879`
- `exact_quote`: “Samsung anticipates that its HBM sales will more than triple in 2026 compared to 2025, and is proactively expanding its HBM4 production capacity.”
- `page/section/text_offset`: Web article, market introduction/roadmap paragraph
- `publisher`: Samsung Electronics
- `source_type`: Official company news release
- `publication_date`: 2026-02-12
- `data_as_of`: 2026 outlook
- `retrieved_at`: 2026-08-15
- `stance`: `supports`
- `limitations`: 前瞻性预测和扩产计划；不能作为已实现销售、可售产出或持续份额增长证据。

### `EV-HBM-11`

- `claim_id`: `DEF-AMKR-01`, `REP-AMKR-01`
- `source_id`: `HBM-08`
- `content_sha256`: `58CB2C3CB514DFF002BC8743EFD40225F31DC66FEC2D93BC2D5B76C6CA1B6D77`
- `exact_quote`: “We successfully ramped our first high density fan out programs into high volume production, expanding across multiple customers and positioning our platform for strong tailwinds in 2026.”
- `page/section/text_offset`: PDF p.3, prepared remarks
- `publisher`: Amkor Technology; transcript by LSEG and hosted by Amkor IR
- `source_type`: Company earnings-call transcript
- `publication_date`: 2026-02-09
- `data_as_of`: FY2025/Q4 2025 call
- `retrieved_at`: 2026-08-15
- `stance`: `supports`
- `limitations`: 证明 Amkor HDFO 平台已有高量产项目；未把这些项目绑定为可替代 TSMC CoWoS 的同一目标产品。

### `EV-HBM-12`

- `claim_id`: `DEF-AMKR-01`, `REP-AMKR-01`
- `source_id`: `HBM-08`
- `content_sha256`: `58CB2C3CB514DFF002BC8743EFD40225F31DC66FEC2D93BC2D5B76C6CA1B6D77`
- `exact_quote`: “We have two additional programs and final qualification for HDFO supporting AI data centers...”
- `page/section/text_offset`: PDF p.3, prepared remarks
- `publisher`: Amkor Technology; transcript by LSEG and hosted by Amkor IR
- `source_type`: Company earnings-call transcript
- `publication_date`: 2026-02-09
- `data_as_of`: 2026 program outlook
- `retrieved_at`: 2026-08-15
- `stance`: `supports`
- `limitations`: Transcript 语法可能存在转录误差；可保守解释为项目处于 final qualification，但不得标为 `qualification_complete`。

### `EV-HBM-13`

- `claim_id`: `EARN-AMKR-01`, `EARN-AMKR-02`
- `source_id`: `HBM-08`
- `content_sha256`: `58CB2C3CB514DFF002BC8743EFD40225F31DC66FEC2D93BC2D5B76C6CA1B6D77`
- `exact_quote`: “Advanced packaging revenue also set a new record, growing 7% year on year, driven by growth in computing, automotive, and consumer.” and “2026 CapEx is expected to increase to a range of $2.5 billion to $3 billion. 65% to 70% is projected for facility expansion, including Phase 1 of our Arizona campus. About 30% to 35% is projected for HDFO, test, and other advanced packaging capacity.”
- `page/section/text_offset`: PDF p.4 and p.5, prepared remarks
- `publisher`: Amkor Technology; transcript by LSEG and hosted by Amkor IR
- `source_type`: Company earnings-call transcript
- `publication_date`: 2026-02-09
- `data_as_of`: FY2025 actuals and 2026 outlook
- `retrieved_at`: 2026-08-15
- `stance`: `supports`
- `limitations`: 收入口径为 advanced packaging 整体，资本开支也混合 HDFO、test 和其他先进封装；不能计算 HDFO 独立利润率。

### `EV-HBM-14`

- `claim_id`: `REP-AMKR-01`, `EARN-AMKR-01`
- `source_id`: `HBM-08`
- `content_sha256`: `58CB2C3CB514DFF002BC8743EFD40225F31DC66FEC2D93BC2D5B76C6CA1B6D77`
- `exact_quote`: “if we look at the 2.5D and HDFO platforms, we're expecting that to nearly triple over the course of this year.” and “The other one is also ramping, hard to project if it'll really be full volume towards the end of the year, but definitely meaningful revenue contribution.”
- `page/section/text_offset`: PDF p.7, Q&A
- `publisher`: Amkor Technology; transcript by LSEG and hosted by Amkor IR
- `source_type`: Company earnings-call transcript
- `publication_date`: 2026-02-09
- `data_as_of`: 2026 outlook
- `retrieved_at`: 2026-08-15
- `stance`: `supports`
- `limitations`: 前瞻性管理层陈述，不是已实现的 2026 全年收入，也没有给出 2.5D/HDFO 基数和产品级利润。

### `EV-HBM-15`

- `claim_id`: `DEF-AMKR-01`, `DEF-AMKR-02`, `EARN-AMKR-02`
- `source_id`: `HBM-08`
- `content_sha256`: `58CB2C3CB514DFF002BC8743EFD40225F31DC66FEC2D93BC2D5B76C6CA1B6D77`
- `exact_quote`: “they're existing customers, but one of them is new to the HDFO platform.”
- `page/section/text_offset`: PDF p.13, Q&A continuing from p.12
- `publisher`: Amkor Technology; transcript by LSEG and hosted by Amkor IR
- `source_type`: Company earnings-call transcript
- `publication_date`: 2026-02-09
- `data_as_of`: 2026 program outlook
- `retrieved_at`: 2026-08-15
- `stance`: `supports`
- `limitations`: 证明两个项目与现有客户有关、其中一个客户首次使用 HDFO；客户未具名，也没有证明是从 CoWoS 切换。

### `EV-HBM-16`

- `claim_id`: `EARN-MU-02`
- `source_id`: `HBM-02`
- `content_sha256`: `A3CE62B84A059E35FAE80C2BFD5C89F9AF334193FD6ACEFF698FC3008E7D4C27`
- `exact_quote`: “Prices increased in the low-60s percentage range, driven by tight industry conditions and favorable mix.” and “The consolidated gross margin for fiscal Q3 was 84.9%, up 10 percentage points sequentially.”
- `page/section/text_offset`: PDF p.8, “DRAM” and “Gross margin”
- `publisher`: Micron Technology
- `source_type`: Official earnings prepared remarks
- `publication_date`: 2026-06-24
- `data_as_of`: FY2026 Q3
- `retrieved_at`: 2026-08-15
- `stance`: `supports`
- `limitations`: 强财务传导证据，但为 DRAM 和公司整体口径，不能把全部价格与毛利改善归因于 HBM4。

### `EV-HBM-17`

- `claim_id`: `EARN-AMKR-02`
- `source_id`: `HBM-08`
- `content_sha256`: `58CB2C3CB514DFF002BC8743EFD40225F31DC66FEC2D93BC2D5B76C6CA1B6D77`
- `exact_quote`: “I think another dynamic is space.” and “And then obviously, the equipment delivery, as Megan said, that will be more front-end loaded in the year to make sure that we're able to support these second-half launches.”
- `page/section/text_offset`: PDF p.12, Q&A
- `publisher`: Amkor Technology; transcript by LSEG and hosted by Amkor IR
- `source_type`: Company earnings-call transcript
- `publication_date`: 2026-02-09
- `data_as_of`: 2026 program outlook
- `retrieved_at`: 2026-08-15
- `stance`: `supports`
- `limitations`: 证明空间和设备交付限制的管理层表述；不量化产能损失、延迟概率或利润影响。

## 6. Claim Registry

### 6.1 Segment Chokepoint Claims

| claim_id | Claim | 状态 | 评分维度 | evidence_id | 允许结论与限制 |
| --- | --- | --- | --- | --- | --- |
| `SEG-HBM-01` | DGX B200 系统规格明确包含 1,440 GB HBM3e 和 64 TB/s HBM3e 带宽。 | `supported` | `downstream_criticality`, `demand_pressure` context | `EV-HBM-01` | 可证明 HBM 是系统明确配置；不能证明短缺、供应商身份或采购份额。 |
| `SEG-HBM-02` | AI 系统性能在架构上依赖内存子系统性能和容量。 | `supported` | `downstream_criticality` | `EV-HBM-02` | 可支持关键性；供应商自述使证据质量上限为 `medium`，直至获得客户侧交叉验证。 |
| `SEG-HBM-03` | HBM 代际增长通过更高 trade ratio 对非 HBM 供给形成挤压。 | `supported` | `capacity_inelasticity`, `constraint_persistence` | `EV-HBM-03` | 只支持方向性权衡；不能量化 HBM 专属供需缺口。 |
| `SEG-HBM-04` | 绿地 fab 的建设、技术工人、许可、能源和工艺复杂度限制短期供给弹性。 | `supported` | `capacity_inelasticity`, `constraint_persistence` | `EV-HBM-04` | 可证明多重约束存在；精确扩产周期和 HBM 可售产出仍未知。 |
| `SEG-HBM-05` | CoWoS 在 2025 年因客户需求而扩产，且扩产后可售产出足以缓解 2.5D 卡点。 | `unknown` | `capacity_inelasticity`, `constraint_persistence` | `HBM-05 pending` | 只定位到扩产陈述，未冻结快照；“产能翻倍”也不能证明良率、认证和可售产出。 |
| `SEG-HBM-06` | Samsung、Micron 和 Amkor 的量产/收入/平台进展构成“单一在位者永久不可替代”的反证搜索结果。 | `supported` | `effective_supply_concentration`, `substitute_weakness` | `EV-HBM-05`, `EV-HBM-09`, `EV-HBM-11` | 只证明替代路线或竞争供给存在；不能证明它们在同一客户、同一 GPU/ASIC、同一时间窗内可互换。 |
| `SEG-HBM-07` | 当前客户批准产能、真实可售余量以及同一产品范围内的合格供应商集中度。 | `unknown` | `effective_supply_concentration`, `qualification_barrier` | None | 禁止用总 wafer capacity、公司市场份额或扩产金额填补。 |

### 6.2 Company Defensibility Claims

| claim_id | Claim | 状态 | 公司/维度 | evidence_id | 允许结论与限制 |
| --- | --- | --- | --- | --- | --- |
| `DEF-SKH-01` | SK hynix 已完成 HBM4 开发并公开表示量产系统/准备已就绪。 | `supported` | SK hynix; `technical_performance_gap`, `qualified_effective_capacity` context | `EV-HBM-07`, `EV-HBM-08` | 只能证明供应商所述技术和 readiness；不能给客户锁定或交付高分。 |
| `DEF-SKH-02` | SK hynix HBM4 的具名客户认证、单供/双供、采购份额和切换行为。 | `unknown` | SK hynix; `qualification_lock_in`, `switching_cost`, `customer_sourcing_evidence` | None | `customer_sourcing_evidence` 固定为 `unknown`，不得由“according to customer plans”替代。 |
| `DEF-SAM-01` | Samsung 已开始 HBM4 量产并向客户商业出货。 | `supported` | Samsung; `qualified_effective_capacity`, `quality_delivery_reliability` context | `EV-HBM-09` | 可支持生产和交付里程碑；不能证明持续交付可靠性或相对良率优势。 |
| `DEF-SAM-02` | Samsung HBM4 客户身份、认证范围、客户内采购份额和持续份额变化。 | `unknown` | Samsung; `qualification_lock_in`, `customer_sourcing_evidence` | None | 客户未具名；三倍销售预期不是客户采购证据。 |
| `DEF-MU-01` | Micron HBM4 已形成超过 10 亿美元收入，证明存在规模交付和产品级收入。 | `supported` | Micron; `qualified_effective_capacity`, `quality_delivery_reliability` context | `EV-HBM-05` | 可支持规模商业化；不能从收入额反推特定客户份额或持续份额增长。 |
| `DEF-MU-02` | Micron 的 SCA 证明 HBM 客户单供、长期绑定或无法切换。 | `unknown` | Micron; `switching_cost`, `customer_sourcing_evidence` | `EV-HBM-06` (`context_only`) | SCA 口径混合 DRAM/HBM/NAND，且 HBM 只写 “as appropriate”；禁止升级为 HBM 锁定证据。 |
| `DEF-TSMC-01` | TSMC CoWoS 客户需求和扩产足以证明公司控制大部分合格可售产出。 | `unknown` | TSMC; `qualified_effective_capacity` | `HBM-05 pending` | 尚缺快照、统一产能口径、良率、客户批准产能和替代者可用容量。 |
| `DEF-TSMC-02` | CoWoS 客户需要长期重新认证且当前没有合格双供或可切换路线。 | `unknown` | TSMC; `qualification_lock_in`, `switching_cost`, `customer_sourcing_evidence` | None | 这是 `durable_chokepoint_owner` 的关键缺口，不能从市场地位或扩产反推。 |
| `DEF-AMKR-01` | Amkor HDFO 已有多客户高量产项目，另有 AI 数据中心项目处于 final qualification/计划 2026H2 放量。 | `supported` | Amkor; `qualification_progress`, `capacity_readiness`, `customer_adoption` context | `EV-HBM-11`, `EV-HBM-12`, `EV-HBM-15` | 证明平台不是纯概念；未来项目仍不能写成认证完成或已放量。 |
| `DEF-AMKR-02` | Amkor HDFO/2.5D 与 TSMC CoWoS 是同一目标产品上的低摩擦可替代路线。 | `unknown` | Amkor vs TSMC; `performance_parity`, `ecosystem_compatibility`, `switching_cost` | `EV-HBM-15` (`context_only`) | 现有客户首次使用 HDFO不等于从 CoWoS 切换；缺同一芯片、封装规格、客户认证和 TCO 对照。 |

### 6.3 Replacement Momentum Claims

| claim_id | Claim | 状态 | 预期成熟度 | evidence_id | 评分边界 |
| --- | --- | --- | --- | --- | --- |
| `REP-SKH-01` | SK hynix HBM4 的公开证据止于开发完成和量产准备。 | `supported` | `prototype`（保守可证下限） | `EV-HBM-07`, `EV-HBM-08` | 合同没有 “mass-production readiness” 枚举；无客户送样/验证原文时不得上调至 `customer_sample`，更不得写 `mass_production`。 |
| `REP-SAM-01` | Samsung HBM4 已量产并向客户商业出货。 | `supported` | `mass_production` | `EV-HBM-09` | 不上调到 `material_revenue` 或 `sustained_share_gain`，因为没有已实现收入和份额原文。 |
| `REP-MU-01` | Micron HBM4 已形成超过 10 亿美元收入。 | `supported` | `material_revenue` | `EV-HBM-05` | 不上调到 `sustained_share_gain`；收入存在不等于份额连续提升。 |
| `REP-AMKR-01` | 对“Amkor HDFO 替代 CoWoS”这一关系，当前最保守可用阶段是客户验证中。 | `supported` | `customer_validation` | `EV-HBM-11`, `EV-HBM-12`, `EV-HBM-15` | 已有 HDFO 高量产项目不自动迁移到目标替代关系；两个 AI 数据中心项目仍在 final qualification，不能写 `qualification_complete`。 |
| `REP-ALL-01` | 任一候选已在统一产品和客户范围内形成持续份额增长。 | `unknown` | 最高阶段不得标 `sustained_share_gain` | None | 需要至少两个可比时点、统一分母、同一产品/客户范围和份额来源。 |
| `REP-ALL-02` | Samsung、Micron 或 Amkor 已在同一客户、同一 GPU/ASIC 世代中实际替代原供应商。 | `unknown` | 不得认定 `realized_replacement` | None | 未具名客户和未披露采购迁移不能支持 realized replacement；供应商并存也不等于发生替代。 |

### 6.4 Earnings Transmission Claims

| claim_id | Claim | 状态 | 公司/维度 | evidence_id | 允许结论与限制 |
| --- | --- | --- | --- | --- | --- |
| `EARN-MU-01` | Micron 已确认超过 10 亿美元 HBM4 收入。 | `supported` | Micron; `segment_revenue_exposure`, `time_to_revenue` | `EV-HBM-05` | 支持 HBM4 已形成实质收入；未披露占公司收入比例和独立利润。 |
| `EARN-MU-02` | Micron 的 DRAM 价格、收入和毛利在紧供给环境中出现强传导。 | `supported` | Micron; `pricing_power`, `margin_transmission` context | `EV-HBM-16` | 只能证明 DRAM/公司整体传导；HBM 对价格和毛利增量的独立贡献仍未知。 |
| `EARN-AMKR-01` | Amkor 的先进封装收入增长、2.5D/HDFO 放量计划和客户承诺构成可见收入路径。 | `supported` | Amkor; `segment_revenue_exposure`, `volume_capacity_leverage`, `time_to_revenue` context | `EV-HBM-13`, `EV-HBM-14`, `EV-HBM-15` | 收入口径混合，2026 增长为前瞻陈述；不足以计算 HDFO 独立 Revenue Exposure 等级。 |
| `EARN-AMKR-02` | 大额前置 CapEx、设备交付和空间限制可能削弱 Amkor 的短期利润传导。 | `supported` | Amkor; `capital_intensity`, `margin_transmission`, `time_to_revenue` | `EV-HBM-13`, `EV-HBM-17` | 支持风险方向；尚不能量化 HDFO 项目回报率、折旧节奏或最终增量利润率。 |
| `EARN-TSMC-01` | CoWoS 的独立收入占比、增量毛利、资本回报和确认时点。 | `unknown` | TSMC; all Earnings dimensions | None | 卡点成立不能替代产品级财务暴露；当前不得输出 `material_earnings_path` 或 `weak_earnings_capture`。 |

## 7. 成熟度对照

| 对象 | 原文里程碑 | 合同允许的当前阶段 | 明确禁止的上调 | 原因 |
| --- | --- | --- | --- | --- |
| SK hynix HBM4 | 开发完成、量产准备、按客户计划供应 | `prototype`（公开原文可证的保守阶段） | `customer_sample` 及以上 | 未找到客户送样、验证、商业出货或收入原文；readiness 不等于生产发生 |
| Samsung HBM4 | 已开始量产并向客户商业出货 | `mass_production` | `material_revenue`, `sustained_share_gain` | 未披露已实现收入和统一口径份额变化 |
| Micron HBM4 | 12-high volume ramp；已实现超过 10 亿美元收入 | `material_revenue` | `sustained_share_gain` | 收入不等于连续份额提升 |
| Amkor HDFO（既有项目） | 多客户 high volume production | `mass_production`，仅适用于既有 HDFO 项目自身 | 将该阶段迁移到“替代 CoWoS”关系 | 产品、客户和封装规格没有对齐 |
| Amkor HDFO（两个 AI 数据中心项目） | final qualification；计划 2026H2 high volume | `customer_validation` | `qualification_complete`, `pilot_volume`, `mass_production` | final qualification 尚未完成，未来放量不是已发生事实 |
| Amkor HDFO -> CoWoS 替代关系 | 一个现有客户首次使用 HDFO | `customer_validation` | `replacement_ready`, `realized_replacement` | 缺同一产品性能/TCO、客户切换、规模可售产出和份额迁移证据 |

这张表故意同时保留“产品自身成熟度”和“替代关系成熟度”。Agent 必须按 `assessment_scope` 选择正确一行，不能把某平台在 A 项目的量产事实迁移到 B 项目的替代判断。

## 8. 五类 Golden 标签的当前预期

| Golden 类别 | 当前测试对象 | 预期结果 | 必须拒绝的错误输出 | 升级为正式 Gold 还缺什么 |
| --- | --- | --- | --- | --- |
| 真实 `durable_chokepoint_owner` | SK hynix、TSMC 等在位者候选 | `仍待证`；当前只能是 `competition_state_uncertain` | 仅凭市场地位、技术领先或扩产直接输出 durable owner | 当前有效的客户单供/依赖、切换成本、认证周期，以及所有已识别挑战者均未 ready 的证据 |
| `contested_chokepoint_owner` | 在位 HBM 供应商 vs Samsung/Micron；TSMC vs 其他先进封装路线 | `仍待评分`；竞争反证已成立，但 incumbent Defensibility 未完成 | 看到多家供应商就直接输出 contested owner | 统一产品世代、客户范围、在位者 Defensibility 和至少一个挑战者完整 Replacement 评分 |
| 高份额但防御证据不足的龙头 | 任一仅有份额/规模/“领先”叙事的在位者 | 应拒绝 `durable_chokepoint_owner`，输出 `competition_state_uncertain` | 把份额、公司规模、专利或管理层措辞作为不可替代性 | 客户侧采购与切换证据；该类可作为 v1 的明确负例 |
| 有技术/认证进展但未 `replacement_ready` 的挑战者 | Amkor HDFO -> CoWoS 替代关系 | 当前预期 `replacement_uncertain`；可作为 `credible_challenge` 候选，但不能先通过 | 把 final qualification、CapEx 或计划 high volume 写成 `replacement_ready`/`realized_replacement` | Performance parity、TCO、同一目标客户认证完成、合格可售产出和客户采用评分 |
| 真卡点但 `weak_earnings_capture` | TSMC CoWoS 财务暴露候选 | 当前只能 `earnings_path_uncertain`，尚不足以标 weak | 卡点成立后自动给 `material_earnings_path`；或因收入未拆分就武断给 weak | CoWoS 独立收入、毛利、CapEx/折旧、确认时点；只有满足合同 `score_max < 50` 等规则后才能标 weak |

结论：当前 28 个 Claim 足以测试 Agent 是否会过度升级证据，但还不足以让五类 Golden 标签全部形成正例。特别是 `durable_chokepoint_owner` 和 `weak_earnings_capture`，当前应把“拒绝误判”作为验收结果，而不是强行造一个正例。

## 9. 必须保持 unknown 的字段

| 字段/问题 | 当前值 | 禁止填充方式 | 合格的新证据 |
| --- | --- | --- | --- |
| HBM 客户批准供应商清单 | `unknown` | 供应商说“满足客户需求” | 客户/平台方具名认证、正式合格供应商或采购文件 |
| HBM 单供/双供状态 | `unknown` | 市场份额或多家供应商同时量产 | 客户侧双供、采购拆分、切换或风险披露原文 |
| 同一 GPU/ASIC 的供应商替代 | `unknown` | 不同客户、不同世代的商业出货 | 同一产品世代的认证前后、采购份额或正式 design win 迁移 |
| SK hynix HBM4 客户采用阶段 | `unknown` | “according to customer plans” | 客户 sample/validation/qualification/production 原文 |
| Samsung HBM4 material revenue | `unknown` | 2026 HBM sales 预期 | 已实现 HBM4 收入、出货价值或财务拆分 |
| 任一供应商 `sustained_share_gain` | `unknown` | 单期收入、增速或扩产 | 至少两个可比时点的同口径份额及分母 |
| CoWoS 合格可售余量 | `unknown` | 名义产能翻倍 | 良率、认证、利用率、可售产出和客户需求的统一口径 |
| Amkor HDFO 对 CoWoS 的性能/TCO 等价性 | `unknown` | 都属于 2.5D/先进封装 | 同一芯片/封装规格的性能、良率、可靠性、成本和客户验证 |
| CoWoS 独立收入和利润 | `unknown` | TSMC 公司总收入或先进技术总口径 | 产品级收入、毛利、资本开支和折旧/回报披露 |

## 10. Agent 验收断言

运行 Golden Case 时至少执行以下断言：

```text
ASSERT customer_sourcing_evidence == unknown
  WHEN only supplier-side HBM4 evidence is available

ASSERT maturity(SK_hynix_HBM4) != mass_production
  WHEN evidence says development complete / mass-production readiness only

ASSERT maturity(Samsung_HBM4) == mass_production
  AND maturity(Samsung_HBM4) != material_revenue

ASSERT maturity(Micron_HBM4) == material_revenue
  AND maturity(Micron_HBM4) != sustained_share_gain

ASSERT replacement_state(Amkor_HDFO -> TSMC_CoWoS) NOT IN
  {replacement_ready, realized_replacement}
  WHEN same-product equivalence and completed target-customer qualification are unknown

ASSERT competition_state != durable_chokepoint_owner
  WHEN customer sourcing behavior is unknown

ASSERT earnings_state(TSMC_CoWoS) == earnings_path_uncertain
  WHEN product-level revenue and time-to-revenue are unknown
```

评分器若违反任何一条，不应通过 v1 Golden Case，即使最终总分看起来合理。

## 11. 下一轮补证队列

按信息价值排序：

1. 客户/平台侧的 HBM4 认证、双供、切换或供应商组合原文；若公开资料确实不存在，固定 `unknown` 作为预期答案。
2. 同一 GPU/ASIC 世代中 Samsung、Micron、SK hynix 的认证和采购迁移证据。
3. TSMC Q1 2025 transcript 的可复现 PDF 快照、页码和内容哈希；之后再补 CoWoS 可售产出、良率和客户批准口径。
4. Amkor 两个 AI 数据中心 HDFO 项目在 2026H2 之后的 qualification、出货和已实现收入更新。
5. Amkor HDFO 与 CoWoS 在同一封装规格上的性能、可靠性、TCO、生态兼容和客户认证对照。
6. TSMC CoWoS 产品级收入、利润、资本强度和收入确认时点；若无法公开获得，Earnings 保持 `unknown`。

## 12. 本版本不做的事

- 不冻结公司总分、排名、目标价或买卖建议；
- 不用当前市场份额替代 Defensibility；
- 不把不同 HBM 世代、不同 GPU/ASIC、不同客户的证据混成一个替代结论；
- 不把 HBM、HBM assembly/test 和 2.5D integration 混成单一 Segment；
- 不因某条 Claim 为 `supported` 就自动把对应评分维度设为高分；
- 不修改已冻结的 `theme-chokepoint-scoring-v1`。若 Golden Case 暴露合同枚举或门槛问题，应另开 v2 变更提案。
