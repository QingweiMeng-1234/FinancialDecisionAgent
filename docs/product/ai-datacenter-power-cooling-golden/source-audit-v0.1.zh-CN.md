# AI 数据中心电力/冷却 Source Audit v0.1

## 1. 结论

固定 Evidence Pack 共 35 条原文、20 个来源事件。所有网页原句使用 UTF-8 `exact_quote` 哈希；PDF 使用完整文件哈希。NERC 响应在 `%PDF-` 前带 45 字节 HTML 包装，证据包保存去包装后的规范化 PDF 哈希及转换说明。

来源包可以稳定证明三件事：

1. 美国数据中心用电需求显著增长，高压变压器存在持续长交期，数据中心负荷可能快于配套输电和发电建设；
2. Eaton、Vertiv、Siemens Energy、GE Vernova 等的订单、backlog、收入或利润正在受益，但这些公司级/分部级披露不能证明产品不可替代；
3. Modine 当前 Data Centers 收入高度重要，但扩产、供应链和生产低效仍显著压低毛利并占用现金。

## 2. Source family 审计

| Family | Evidence | 可证明 | 不可证明 |
| --- | --- | --- | --- |
| `fam-doe-lbnl-data-center-energy-2024` | E23 | 美国数据中心用电 2023 实际值和 2028 预测区间 | 已签负荷、设备品类订单、供应商份额 |
| `fam-nerc-risc-2025` | E24-E25 | 高压变压器持续长交期；数据中心连续运行、低电能质量容忍、接入速度可快于电网建设 | 精确交期、具名阻断项目、供应商依赖 |
| `fam-eaton-q4-2025` | E01-E02 | Electrical Americas 订单/积压/销售/利润 | 变压器、UPS、PDU 的独立份额或利润 |
| `fam-eaton-transformer-capacity-2025` | E03 | 变压器 critical shortage 和新厂时间表 | 2027 新厂当前已有合格产出 |
| `fam-vertiv-q4-2025` | E04-E05 | 订单、backlog、销售、利润率及 AI/hyperscale 驱动 | 单一产品、客户、地区的份额和不可替代性 |
| `fam-siemens-energy-fy2025` | E06-E07 | Grid Technologies 增长、利润率和数据中心驱动 | 美国变压器产品级收入、份额和客户单供 |
| `fam-ge-vernova-q4-2025` | E08-E09 | Electrification/Power 订单和 backlog 质量改善 | backlog margin 是已确认利润；产品级传导 |
| `fam-modine-q2-fy2026` | E10-E11 | 早期收入增长、毛利和 FCF 拖累 | 当前状态；已由更新季度刷新 |
| `fam-nvent-liquid-cooling-expansion-2026` | E12 | 厂房扩张和投资阶段 | qualification、yield、可售产出、采用、份额 |
| `fam-vertiv-nvidia-gb300-reference-2025` | E13 | GB300 参考架构工程集成 | 生产采用和 actual displacement |
| `fam-nist-euv-2023` | E14 | 2023 EUV scanner 仅 ASML 制造、100% 份额、客户部署 | 未来永久无替代 |
| `fam-asml-annual-report-2025` | E15-E16、E26-E28 | 多期 EUV 销售、关键层性能、HVM 采用、客户工艺集成、当前竞争边界 | 客户量化切换成本；开放世界无未来挑战者 |
| `fam-apple-transition-announcement` | E17-E18 | 迁移计划和 Universal/Rosetta 工具 | 迁移已经完成 |
| `fam-apple-m1-launch-2020` | E29-E31 | 同代商业芯片性能、三款 Mac 上线、应用兼容 | 两年后完整 displacement；需 E19 |
| `fam-apple-silicon-completion` | E19-E20 | Mac 全产品线迁移完成和后续芯片性能 | 独立第三方 benchmark |
| `fam-asetek-hp-water-cooling-qa` | E21 | 历史测试成功但客户未采用 | 当前 2026 公司状态 |
| `fam-supermicro-getworks-case` | E22 | 日本具名数据中心生产部署 | 美国采用、持续周期、份额和替换 |
| `fam-modine-q4-fy2026` | E34 | 40 亿美元长期 hyperscale chiller 协议 | 合同期限、分期收入和利润率 |
| `fam-modine-q1-fy2027` | E32-E33、E35 | 当前分部收入/毛利、FCF、供应链与扩产拖累 | 单项成本精确归因和长期资本回报 |

## 3. 时效与重复计数

- E10-E11 保留为历史轨迹，不再决定 2026-08-15 当前状态；当前裁决以 E32-E35 为主。
- E15、E16、E26-E28 来自同一 ASML 年报，只算一个来源家族，但可拆出不同 `fact_key`。
- E17-E18 同属迁移公告；E19-E20 同属迁移完成事件；不能按四个网页段落伪装成四个独立事件。
- E32、E33、E35 同属 Modine FY2027 Q1 事件，只能算一个独立披露事件。
- E24 与 E25 同属 NERC 报告；分别支持供应约束和下游关键性，但不增加独立来源数。

## 4. 哈希与可复现性检查

- Evidence Pack JSON 可解析；无 `TO_FILL`。
- 所有非 PDF `exact_quote` 的 SHA-256 已按 UTF-8 重算并一致。
- NERC 规范化 PDF SHA-256：`C34C38C41A79BBDC436A7DFA9A58A8E6778AAE7BEE3D1F9AE1514238314AD0FA`。
- ASML 2025 年报 SHA-256：`ADD58BE9D9822CA12584B63C55C089B36E1AE5BF7A1825EBF1D1572628F2C52C`。
- Modine FY2027 Q1 结果 PDF SHA-256：`9972A7E9F913984DECC01EAB99E2C074AC430A7789A3BACFDD1698F700F7A033`。
- Modine FY2026 Q4 结果 PDF SHA-256：`9523826E585FEA61243A109A1E0FCCAE4541ED5DE49D09531C6A5B7A67D3234B`。

## 5. 证明边界

本包能完成跨主题正向召回测试，但不能把主题内任何设备商标为 Durable，也不能把参考架构、扩产或具名部署自动升级为 Realized Replacement。完整重新冻结仍要求真正独立的第二标注员在固定包上盲标。
