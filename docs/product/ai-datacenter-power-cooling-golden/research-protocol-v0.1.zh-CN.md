# AI数据中心电力/冷却 Golden 研究协议 v0.1

## 1. 状态与目的

| 字段 | 值 |
| --- | --- |
| protocol_id | `ai-dc-power-cooling-golden-protocol-v0.1` |
| 状态 | `frozen_for_discovery`；只冻结本轮研究方法，不冻结评分合同 |
| as_of_date | 2026-08-15 |
| 主题 | AI数据中心电力与冷却基础设施 |
| 地区 | 美国为主；其他地区必须拆卡 |
| horizon | 24个月 |
| 人工复核预算 | 最多10张scoped company card |
| 评分候选 | `theme-chokepoint-scoring-v1.1` |

本协议用于构建跨主题Golden、寻找真实正例并检验v1.1候选。研究结果不得为了满足预设标签而降低证据门槛。

## 2. Product Anchor与Segment

```text
product_anchor_1 = 50MW以上AI数据中心园区的通电路径
product_anchor_2 = 高密度AI机柜的关键配电路径
product_anchor_3 = 直接液冷系统的生产部署路径
```

首轮只保留三个Segment：

1. `seg_large_power_transformer_switchgear_us`：大型电力变压器、中高压开关设备及同范围合格产能；
2. `seg_critical_power_distribution_us`：UPS、PDU、Busway及高密度机柜关键配电；
3. `seg_direct_liquid_cooling_us`：CDU、冷板、热交换和同范围生产集成。

公用事业互联排队、电源许可和现场电网容量作为系统约束节点单独记录；它们不能直接变成公司Defensibility分。

## 3. Scope键

每张公司卡必须绑定：

```text
company_id
+ product_id
+ segment_id
+ customer_or_platform_scope
+ geography
+ time_horizon_months=24
+ as_of_date=2026-08-15
+ metric_definition
```

跨客户、平台、地区、期间、分母或产品的事实不得拼接。一个公司跨Segment分别计卡。

## 4. Discovery与Review预算

- Discovery Pool：最多30家公司或路线；
- Review Candidate：最多10张卡；
- Lane：`proven_floor=6`、`bounded_upside=2`、`counterexample_or_transfer=2`；
- 同一Segment原则上最多5张，每个重要Segment至少1张；
- 全unknown、纯市场预测、纯CapEx或纯政策对象不能入Review。

## 5. 来源协议

优先级：

```text
客户/监管/公共机构原文
> SEC/年度或季度财报/正式投资者材料
> 官方产品、认证、标准和项目文档
> 公司托管transcript
> 高质量媒体，仅用于发现
```

最终Claim必须保存URL、publisher、publication_date、data_as_of、retrieved_at、exact_quote、page/section、content_sha256、origin_event_id、evidence_family_id和证明限制。

同一管理层披露的新闻稿、电话会、转载和聚合页只算一个证据家族。搜索摘要、模型转述和不可复现页面不能计分。

## 6. 查询组

每个候选必须执行并记录：

1. 正向：capacity、qualification、customer adoption、production、revenue；
2. 反证：competitor、dual source、alternative、cancelled、delay、price pressure；
3. 财务：segment revenue、margin、CapEx、depreciation、working capital、cash flow；
4. 迁移：replace、remove、transition、share shift、primary supplier、second source。

## 7. 正例Gate

### Durable

- incumbent Defensibility=`high_defensibility`；
- Customer Sourcing当前有效且supported；
- 冻结、可复现的`challenger_set`完成；
- 集合内没有达到`credible_challenge`的挑战者；
- 开放网络未搜到不能证明Durable。

### Realized Replacement

Performance、Qualification、Capacity、Adoption、Ecosystem和Displacement必须全部supported且`rating_min>=3`，Scope一致、当前有效。收入、市场份额增长或总分不能补偿。

### Weak Earnings

必须先证明Segment卡点和公司业务暴露，再由受支持的Margin Transmission硬失败、资本/现金负担否定期限内利润捕获，或充分解析的低分上界形成`weak_earnings_capture`。

## 8. 双标注与重新冻结Gate

两名标注员使用同一固定Evidence Pack、彼此不可见结果，独立输出谓词、区间、Hard Gate和唯一`primary_state`。裁决必须区分标注错误、原文歧义、Scope错误、锚点歧义和合同缺陷。

建议验收线，等待产品负责人最终批准：

- HBM 15条边界断言100%通过；
- Hard Gate一致率`>=95%`，Durable/Realized误升级为0；
- 最终状态一致率`>=90%`；
- 有序维度weighted kappa`>=0.70`；
- 所有分歧100%裁决；
- Top-10人工认可候选召回`>=80%`；
- 同一事实主要高档重复计分和同源误计独立来源均为0。

## 9. Stop Rule

若本主题找不到真实Durable、公司级Realized或Weak Earnings正例，必须记录`no_gold_found`及搜索覆盖，并增加补充真实案例。禁止降低Gate、把unknown补成中性分或预先指定某家公司为正例。
