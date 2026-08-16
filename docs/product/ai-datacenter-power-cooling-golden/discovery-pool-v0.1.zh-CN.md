# AI 数据中心电力/冷却 Discovery Pool v0.1

## 1. 口径

- `as_of_date=2026-08-15`，美国为主，24 个月；日本部署单独拆卡。
- Discovery 对象是 `company + product + segment + geography`，不是股票推荐。
- `入选`只表示值得人工审阅，不代表通过 Confirmed Gate。
- 本轮共 30 个对象；Review 预算 10 张卡，分配为 6/2/2。

## 2. Discovery 清单

| # | 对象 | Segment | 发现信号 | 反证/缺口 | 处理 |
| ---: | --- | --- | --- | --- | --- |
| 1 | Eaton / 美国三相变压器 | 大型变压器/开关 | 明示 critical shortage、3.4 亿美元扩产、2027 投产 | 新厂计划不等于当前合格产出；无产品份额和客户单供 | 入选 proven_floor |
| 2 | Siemens Energy / Grid Technologies | 大型变压器/开关 | FY2025 收入 +25.4%、利润率 15.8%，数据中心驱动电网投资 | 无美国产品级份额和客户采购 | 入选 proven_floor |
| 3 | GE Vernova / Electrification | 大型变压器/开关 | 订单 +65%，Power/Electrification backlog 增长 | backlog margin 不是已确认利润；产品范围过宽 | 入选 proven_floor |
| 4 | Hitachi Energy / 大型变压器 | 大型变压器/开关 | 全球扩产和电网设备线索 | 本轮固定包缺同范围美国原文 | 保留，未入 Review |
| 5 | Schneider Electric / 中高压开关 | 大型变压器/开关 | 数据中心电力组合完整 | 缺产品级合格产能、份额和迁移证据 | 保留，未入 Review |
| 6 | Mitsubishi Electric / 变压器 | 大型变压器/开关 | 北美产能路线线索 | 固定包无当前美国客户原文 | 保留，未入 Review |
| 7 | Prolec GE / 大型变压器 | 大型变压器/开关 | 北美制造足迹 | 私有范围披露有限，缺可比财务分母 | 保留，未入 Review |
| 8 | Virginia Transformer / 大型变压器 | 大型变压器/开关 | 美国本土专业供应 | 私有公司，公开有效产出与采购证据不足 | 保留，未入 Review |
| 9 | Hyundai Electric / 电力变压器 | 大型变压器/开关 | 美国需求与订单线索 | 地区/产品份额口径不足 | 保留，未入 Review |
| 10 | Powell Industries / 开关设备 | 大型变压器/开关 | 数据中心订单线索 | 当前固定包缺可复现产品原文 | 保留，未入 Review |
| 11 | Vertiv / critical power | 关键配电 | 美洲和 hyperscale 订单强，backlog 150 亿美元；利润率改善 | 公司范围包含冷却等多产品；无客户单供 | 入选 proven_floor |
| 12 | Eaton / critical power distribution | 关键配电 | Electrical Americas 订单、销售和利润同步增长 | 不能从分部结果拆出 UPS/PDU/Busway | 入选 proven_floor |
| 13 | Schneider Electric / UPS/PDU | 关键配电 | 产品组合和客户基础线索 | 固定包无产品级当前原文 | 保留，未入 Review |
| 14 | ABB / 数据中心配电 | 关键配电 | 中低压和电能质量产品线索 | 缺同范围采用、份额和财务暴露 | 保留，未入 Review |
| 15 | Legrand / busway/PDU | 关键配电 | 数据中心配电组合 | 固定包无原文；客户替换成本未量化 | 保留，未入 Review |
| 16 | Delta / UPS | 关键配电 | UPS 与数据中心电源产品 | 美国客户和合格产出不透明 | 保留，未入 Review |
| 17 | Socomec / UPS | 关键配电 | 专业 UPS 路线 | 公开美国收入/份额不足 | 保留，未入 Review |
| 18 | nVent / 电气连接与机柜配电 | 关键配电 | 数据中心产能扩张线索 | E12 主要对应液冷，不能迁移到配电高档 | 保留，未入 Review |
| 19 | Siemens / 数据中心配电 | 关键配电 | 广泛电气组合 | 不得与 Siemens Energy Grid Technologies 混卡 | 保留，未入 Review |
| 20 | Vertiv / GB300 液冷参考架构 | 直接液冷 | 与 NVIDIA GB300 参考架构完成工程集成 | reference architecture 不等于客户生产采用 | 入选 proven_floor |
| 21 | nVent / 液冷制造 | 直接液冷 | 三年第三次扩产，新增 16 万平方英尺 | 厂房面积不证明合格可售产出、利用率或份额 | 入选 bounded_upside |
| 22 | Supermicro / rack-scale DLC / 日本 | 直接液冷 | Getworks 具名生产部署 | 非美国；无两周期、份额或 displacement | 入选 bounded_upside |
| 23 | Modine / Data Centers cooling | 直接液冷/热管理 | FY2027 Q1 分部收入 3.486 亿美元、同比 +90% | 毛利率下降 960bp，FCF 为负；当前利润捕获弱 | 入选 counterexample |
| 24 | Asetek / HP 水冷历史测试 | 直接液冷 | HP 已测试且知道产品有效 | 明确“no demand, aren't adopting”；未注明日期，当前状态 stale | 入选 counterexample |
| 25 | CoolIT / DLC | 直接液冷 | 生产部署线索 | 私有公司，固定包无可复现财务和迁移原文 | 保留，未入 Review |
| 26 | Boyd / 液冷 | 直接液冷 | 冷板/CDU 制造线索 | 无当前客户采用和合格产出分母 | 保留，未入 Review |
| 27 | Schneider Electric / Motivair | 直接液冷 | 收购后液冷组合线索 | 收购和产品组合不等于生产采用或份额迁移 | 保留，未入 Review |
| 28 | Johnson Controls / Silent-Aire | 直接液冷/热管理 | 数据中心热管理客户基础 | 缺本轮同范围直接液冷原文 | 保留，未入 Review |
| 29 | ZutaCore / 两相液冷 | 直接液冷 | 替代架构线索 | 未证明同范围规模合格产出和客户 displacement | 保留，未入 Review |
| 30 | 富士通/NEC 等服务器内液冷集成路线 | 直接液冷 | 系统级集成替代线索 | 与美国设施侧 CDU/冷板 Scope 不同，不能合并 | 排除，Scope 不同 |

## 3. Review 选择

| Lane | 卡片 |
| --- | --- |
| `proven_floor` | Eaton 变压器；Siemens Energy Grid Technologies；GE Vernova Electrification；Vertiv critical power；Eaton critical power；Vertiv GB300 液冷参考架构 |
| `bounded_upside` | nVent 液冷扩产；Supermicro 日本 rack-scale DLC |
| `counterexample_or_transfer` | Modine 当前 Weak Earnings；Asetek 历史“测试成功但未采用” |

## 4. Stop-rule 结果

AI 数据中心主题内找到当前真实 `weak_earnings_capture` 正例（Modine），但没有找到满足严格合同的公司级 `durable_chokepoint_owner` 或 `realized_replacement` 正例。按协议追加 ASML EUV 和 Apple Silicon 两个跨主题真实案例；没有降低 Gate。
