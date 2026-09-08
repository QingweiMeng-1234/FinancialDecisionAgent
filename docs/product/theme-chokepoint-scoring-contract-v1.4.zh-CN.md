# Theme Chokepoint Scoring Contract v1.4

## 1. 状态与继承

| 字段 | 值 |
| --- | --- |
| contract_id | `theme-chokepoint-scoring-v1.4` |
| 状态 | `frozen`（2026-08-16） |
| 基础合同 | v1.2全部评分定义＋v1.3语义校验修复 |
| 机器合同 | `theme-chokepoint-semantic-task-contract-v1.4` |
| 触发事件 | v0.5失败后完成三项P0修复；全新v0.6预裁决总门、HBM 22项回归与Top-10复核全部通过 |

v1.4只修复v0.5识别出的三类系统性问题：客户集中度复合锚点、服务型Replacement本体、虚假exact。未被本文件覆盖的维度、状态优先级、Coverage、Hard Gate和100分预算继续继承v1.2与v1.3。

v1.4已于2026-08-16按预承诺门槛重新冻结。冻结依据是全新v0.6的不可变双盲原始结果先通过预裁决总门，再完成全部分歧裁决、HBM 22项回归和Top-10人工复核；不是由修复实现、单元测试或裁决后一致性替代预裁决证据。

## 2. CF-05-01：拆分客户集中暴露与关系保护

旧维度`customer_concentration_risk`删除，原5%权重拆成两个独立维度：

| 维度 | 权重 | 方向 |
| --- | ---: | --- |
| `customer_concentration_exposure` | 2.5 | 分数越高，收入越分散 |
| `customer_relationship_protection` | 2.5 | 分数越高，合同与切换保护越强 |

### 2.1 Customer Concentration Exposure

使用同期间外部确认收入分母。`top1`、`top2`分别表示最大一个、最大两个客户收入占比；档位按顺序互斥求值。

| 档位 | 锚点 |
| ---: | --- |
| 0 | `top1>=50%`或`top2>=70%` |
| 1 | 未触发0档，且`top1>=30%`或`top2>=50%` |
| 2 | 未触发0–1档，且`top1>=15%`或`top2>=30%` |
| 3 | 未触发0–2档，且`top1>=10%`或`top2>=20%` |
| 4 | `top1<10%`且`top2<20%` |

若只披露一个分母，只能输出该分母直接支持的floor或ceiling；缺少另一个分母不能被当作0，也不能凭“未披露”形成exact。客户名称是否公开不影响集中度求值。

### 2.2 Customer Relationship Protection

| 档位 | 锚点 |
| ---: | --- |
| 0 | 明确可随时取消、压价或内制，且没有最低采购、终止补偿或切换保护 |
| 1 | 明确非约束、无最低采购或短期逐单关系 |
| 2 | 有部分合同期限、切换成本、扩客计划或有限终止保护 |
| 3 | 约束合同、显著切换成本或终止补偿明显限制取消/压价 |
| 4 | 多期约束合同与技术/流程切换保护同时存在，并覆盖主要收入 |

收入占比不能代理合同保护；合同未披露时本维度为unknown，而集中度维度仍独立计分。

## 3. CF-05-02：Replacement模式

每个Replacement Case必须声明：

```json
{
  "assessment_track": "replacement",
  "replacement_mode": "product | service"
}
```

每个Replacement Ordinal任务必须携带与模式、维度一致的`anchor_profile`。product继续使用v1.2产品锚点；service使用以下服务锚点。混用、缺失或自定义profile使任务包无效。

### 3.1 Service Replacement 0–4锚点

| 维度 | 0 | 1 | 2 | 3 | 4 |
| --- | --- | --- | --- | --- | --- |
| Performance Parity | 明确不满足预定义安全、可靠性、可用性或服务质量要求 | 内部/有限试点只满足部分要求，或测试不可比 | 同口径服务指标在一个范围/期间满足全部最低要求 | 一个具名生产运营范围通过官方或独立验证并满足全部预定义要求 | 至少两个地区、平台或可比期间重复达到或超过 |
| Qualification Progress | 商业授权被拒绝、撤销或失效 | 仅测试许可，不能向公众/客户收费运营 | 有限商业试点授权，完整生产授权未完成 | 一个具名司法辖区或平台的付费生产授权当前有效 | Scope内全部必要地区/平台授权当前有效 |
| Capacity Readiness | 服务不可用、关键资源撤回或项目取消 | 资源已部署但合格付费服务产出为零 | 有非零付费生产服务，需求覆盖未知或不足 | 一个期间稳定覆盖同口径已预约/已请求需求，或覆盖`>=10%且<30%`目标需求 | 两个期间/地区覆盖全部同口径已请求需求并有余量，或覆盖`>=30%`目标需求 |
| Customer Adoption | 用户/客户拒绝、取消或停止生产使用 | 付费试点、初始商业行程或初始订单 | 一个具名地区、平台或企业客户进入付费生产使用 | 两个具名地区/平台，或一个具名范围跨两个可比期间持续付费使用 | 多个具名生产范围规模化采用，同时具备规模与连续性原文 |
| Cost/TCO Advantage | 同口径服务TCO高出`>10%` | 高出`>5%且<=10%` | 差异在`±5%` | 降低`>5%且<10%`并经客户或生产周期验证 | 降低`>=10%`并跨两个周期/客户验证 |
| Execution/Delivery | 关键运营承诺失败、资源不足或服务取消 | 只有公告、候补名单或未来城市计划 | 资金、车队/设备、团队或运营资源落实，但里程碑风险高 | 当前范围按计划上线，且有一个相似成功生产范围 | 连续兑现扩张，至少两个相似生产范围成功且承诺有效 |
| Regulatory Tailwind | 生效规则阻止服务 | 显著限制运营范围或增加认证成本 | 官方证据证明中性 | 生效政策降低运营/采购壁垒 | 强制采购、牌照或资金机制直接推动规模采用 |
| Architecture Tailwind | 目标服务架构排斥该路线 | 需要重大平台重建 | 架构兼容但无偏好 | 架构变化降低接入或多供门槛 | 架构明确要求或强烈推动该路线 |
| Ecosystem Compatibility | 无法兼容必要调度、支付、监管、机场、企业或目标平台流程 | 需要重大工具/流程/架构重建 | 需要明显适配或重新认证但可行 | 一个目标市场完成必要接口与流程集成，迁移受控 | 至少两个目标市场通过可复用标准接口、工具和流程低摩擦采用 |

Displacement仍要求同一具名用户、买方或工作负载从在位方案迁移；总行程、总用户、市场增长或候补名单不能代理same-user displacement。Lifecycle Status继续使用v1.2的失败、退出、生产与规模里程碑。

### 3.2 单一主要高档归属

付费运营和使用规模默认主要归属Customer Adoption。除非同一原文span含有可独立拆分的性能比较、官方授权、需求覆盖分母或生态集成事实，否则不得再以同一运营事实升级Performance、Qualification、Capacity或Ecosystem。

## 4. AF-05-01：Bound Basis强制合同

每个Ordinal结果新增：

```json
{
  "bound_basis": {
    "floor_anchor": 3,
    "ceiling_anchor": null,
    "exact_basis": null,
    "unresolved_higher_anchors": [4],
    "excluded_higher_anchors": []
  }
}
```

### 4.1 确定性规则

1. `exact 4`：`floor_anchor=4`、`ceiling_anchor=4`、`exact_basis=natural_cap`，且4档全部谓词完整通过。
2. `exact k`且`k<4`：必须有`floor_anchor=k`和`ceiling_anchor=k`；`exact_basis`只能是`direct_upper_bound`或`contract_exclusivity`；`excluded_higher_anchors`必须覆盖全部`k+1..4`。
3. `lower_bound [k,4]`：`floor_anchor=k`、`ceiling_anchor=null`、`exact_basis=null`，并至少保存一个未解析高档。
4. `upper_bound [0,k]`：必须有受支持`ceiling_anchor=k`；搜索未发现不能形成ceiling。
5. `interval [a,b]`：同时保存受支持floor与ceiling。
6. `unknown [0,4]`：floor、ceiling和exact_basis均为null。

“只有一个期间”“没有第二个客户”“未披露四个季度”等缺口只能进入`unresolved_higher_anchors`，不得进入`excluded_higher_anchors`。

## 5. 机器校验

正式实现：

- `tools/theme-chokepoint/semantic-task-contract-v1.4.json`
- `tools/theme-chokepoint/semantic-validator-v1.4.mjs`
- `tools/theme-chokepoint/validate-submission-v1.4.mjs`
- `tools/theme-chokepoint/semantic-validator-v1.4.test.mjs`

validator必须拒绝：

- Replacement缺少mode或profile错配；
- v1.4 Earnings仍使用旧复合集中度维度；
- 缺少`bound_basis`；
- exact没有自然封顶或受支持上界；
- task权重、Evidence Capability、Hard Gate、Coverage、State与canonical不一致。

## 6. 迁移与重新冻结边界

- v0.1至v0.5的盲包、submission、receipt、agreement和裁决保持原SHA，不回写v1.4语义。
- v1.4只用于全新v0.6及后续任务。
- v0.6不得复用任何已进入旧盲标、裁决或Golden人工审核的公司/产品案例。
- 重新冻结仍须在裁决前同时通过：Hard Gate一致率`>=95%`、Final State一致率`>=90%`、exact/exact至少10对、线性加权kappa`>=0.70`、高风险单边升级为0。
- 只有预裁决总门通过后，才允许运行HBM回归和Top-10人工复核。
- v0.6预裁决结果为Hard Gate 100%、Final State 100%、exact/exact 14对、线性加权kappa 1.0、高风险单边升级0；13项实质Ordinal分歧已由产品负责人裁决，5项State由机器合同机械重算。
- HBM/先进封装22/22项边界回归通过；AI数据中心电力/冷却Top-10按唯一公司复核，10/10 Accepted候选被覆盖、1个Reject反例保留、`missed_candidate=0`、12/12成对关系无明显反序。
- 因所有重新冻结门均通过，`theme-chokepoint-scoring-v1.4`自2026-08-16起为冻结实现基线。任何评分语义变化必须创建新合同版本并重跑Golden、Holdout与Top-10门。
