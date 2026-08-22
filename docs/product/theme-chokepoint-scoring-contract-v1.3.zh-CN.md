# Theme Chokepoint Scoring Contract v1.3

## 1. 状态与继承

| 字段 | 值 |
| --- | --- |
| contract_id | `theme-chokepoint-scoring-v1.3` |
| 状态 | `freeze_candidate` |
| 基础合同 | `theme-chokepoint-scoring-v1.2` |
| 机器合同 | `theme-chokepoint-semantic-task-contract-v1.3` |
| 触发事件 | v0.4预裁决门失败及TD-01、ED-01、TV-01 |

v1.3完整继承v1.2的评分维度、权重、0–4锚点、区间、Coverage、Hard Gate、状态优先级和10卡预算。若本文件与v1.2冲突，以本文件为准。v1.2及v0.4全部封存资产保持不可变。

v1.3仍是候选合同，不因三个P0完成代码修复而自动变为`frozen`。

## 2. TD-01：State任务必须完整覆盖评分族

任何`state_task`必须显式保存：

```json
{
  "item_id": "S...",
  "case_id": "C...",
  "state_family": "earnings",
  "ordinal_item_ids": ["..."],
  "hard_gate_item_ids": ["..."]
}
```

对于该`state_family`的每个加权维度，同一Case必须存在且只存在一个Ordinal任务。没有证据时也必须生成该维度任务，由标注员输出`unknown + none + [0,4]`；禁止省略维度，禁止在State rationale中临时发明未提交的分数。

机器不变量：

1. 加权维度权重必须合计100；
2. `ordinal_item_ids`必须完整覆盖全部加权维度；
3. 每个加权维度基数必须为1；
4. 引用的Ordinal必须与State同Case、同评分族；
5. 引用的Hard Gate必须与State同Case；
6. 缺少任一维度时整个任务包无效，而不是让该State自动withheld后继续盲标。

Competition不是独立100分评分族；它必须读取已经由各自语义validator封存的Defensibility与Replacement状态，不得伪造Competition加权维度。

## 3. ED-01：`revenue production`证据语义

`revenue production`、`commercial production`、`production ramp`和“工厂已投产”默认只具备以下能力：

- `commercial_production_stage`；
- 若原文明确存在非零产出，可具备`nonzero_ramp_output`。

这些短语单独不能证明：

- `accounting_revenue_confirmed`；
- `multi_period_revenue_confirmed`；
- Revenue Materiality任何正向档；
- Time to Revenue任何要求“收入已确认”的档；
- Earnings Persistence。

### 3.1 会计收入能力

`accounting_revenue_confirmed`至少需要以下一种同Scope原文：

1. 财报收入注释或可比稳定分部披露；
2. 明确的产品收入金额或占比；
3. 明确客户销售已经形成确认收入，且不是订单、backlog、预付款或未来承诺。

`multi_period_revenue_confirmed`还必须有满足持续期锚点的多个可比期间。单期已确认收入不能自动升级为多期能力。

Evidence Pack中的每条证据必须保存`claim_capabilities[]`。某维度输出`supported`时，validator必须检查其`primary_evidence_ids`至少包含该维度要求的能力；不满足即拒绝submission，而不是仅提示人工复核。

## 4. TV-01：语义validator

v1.3禁止只做JSON Schema校验。正式validator必须依次执行：

1. 校验task、pack、contract和result绑定；
2. 校验State任务的完整维度覆盖；
3. 校验`supported`标签所需的Evidence Capability；
4. 按v1.2公式重算`score_min`、`score_max`；
5. 重算Presence、Resolved和Decision Coverage；
6. 按任务中的受限谓词表达式重算每个Hard Gate三态标签；
7. 按任务中的有序状态规则重算唯一`primary_state`；
8. 校验`achieved_hard_gates`恰好等于机械通过的Gate集合；
9. 将任一差异作为validation error，禁止生成submission receipt。

### 4.1 新State输出合同

```json
{
  "item_id": "S...",
  "primary_state": null,
  "achieved_hard_gates": [],
  "derived_metrics": {
    "score_min": 0,
    "score_max": 100,
    "presence_coverage": 0,
    "resolved_coverage": 0,
    "decision_coverage": 0
  },
  "withheld_reason": "insufficient_evidence",
  "rationale": "..."
}
```

旧字段`achieved_gates`不再接受自由文本。新字段`achieved_hard_gates`只能包含当前State任务引用且机械结果为`pass`的Hard Gate item_id。

### 4.2 允许的表达式

Hard Gate只允许：`ordinal_resolved`、`ordinal_min`、`and`、`or`。

State只允许：`constant`、`metric_gte`、`metric_lt`、`ordinal_resolved`、`ordinal_min`、`ordinal_max_lt`、`gate_is`、`and`、`or`、`not`。

自由文本策略、未知运算符、跨Case引用和引用未声明Gate均使任务包无效。表达式只负责执行已冻结合同，不允许Holdout设计者在任务中改变v1.3业务阈值。

## 5. 实现绑定

当前候选实现：

- `tools/theme-chokepoint/semantic-task-contract-v1.3.json`；
- `tools/theme-chokepoint/semantic-validator-v1.3.mjs`；
- `tools/theme-chokepoint/validate-submission-v1.3.mjs`；
- `tools/theme-chokepoint/semantic-validator-v1.3.test.mjs`。

CLI：

```powershell
node .\tools\theme-chokepoint\validate-submission-v1.3.mjs `
  --task <annotation-task.json> `
  --pack <evidence-pack.json> `
  --result <annotator-result.json> `
  --semantic-contract .\tools\theme-chokepoint\semantic-task-contract-v1.3.json
```

CLI输出包含四个输入文件的绝对路径、SHA-256、数量、机械派生结果及全部错误，可直接作为receipt之前的validation report。task内嵌权重或Evidence Capability要求与canonical机器合同不一致时必须失败。

## 6. 重新冻结边界

三个P0的实现完成只允许开始构建v0.5。重新冻结仍必须使用全新未解盲案例，并同时通过：Hard Gate一致率、Final State一致率、exact/exact数量、加权kappa及高风险单边升级五个原有门槛。裁决后一致不能替代预裁决门。
