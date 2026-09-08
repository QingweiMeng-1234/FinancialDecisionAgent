# Theme Chokepoint Scoring v1.3 迁移与验证计划

## 1. 决策

v0.4的Hard Gate一致率为85.19%，State一致率为80%，预裁决总门失败。v1.3只修复该轮识别出的三个P0，状态保持`freeze_candidate`。

| P0 | 修复 | 验收 |
| --- | --- | --- |
| TD-01 | State任务完整覆盖评分族全部加权维度 | 缺少任一维度时task validation失败 |
| ED-01 | 证据增加`claim_capabilities[]`；制造阶段与会计收入分离 | 仅引用`commercial_production_stage`不能提交Revenue类supported标签 |
| TV-01 | 重算分数、Coverage、Hard Gate和State | 任一提交值与派生值不同即失败 |

## 2. 版本与不可变边界

- v1.2合同、v0.4盲包、两份submission、receipt、agreement、裁决和post-submission manifest保持原SHA；
- 不重新生成v0.4，不用修复后的validator重写v0.4正式validation report；
- v1.3工具只供全新的v0.5及后续任务使用；
- 当前未修改Theme Research运行代码、评分服务或数据库。

## 3. v0.5生成器要求

1. 从机器合同加载各评分族权重，禁止在生成脚本重复手写另一套权重；
2. 每个State Case自动生成完整加权Ordinal集合；无证据维度仍生成unknown任务；
3. 每条证据必须声明`claim_capabilities[]`；
4. 每个Hard Gate必须有机器谓词；
5. 每个State family必须有受限有序规则，且规则中的Gate和维度全部可解析；
6. 预承诺检查必须在生成盲包前运行语义validator的task-only部分；
7. 两份真人result必须先分别通过结构与语义校验，才能生成receipt。

## 4. 当前验证证据

`node --test tools/theme-chokepoint/semantic-validator-v1.3.test.mjs`覆盖：

- 缺少Capital/Cash维度时拒绝State任务；
- `revenue production`不能替代会计收入能力；
- Hard Gate标签不一致时拒绝；
- State与机械结果不一致时拒绝；
- derived metrics不一致时拒绝；
- 未声明Gate和自由文本State policy被拒绝；
- task内嵌权重或Revenue能力要求偏离canonical机器合同时被拒绝；
- 完整合法fixture通过；
- CLI生成可封存JSON报告。

## 5. 下一门

只有完成v0.5全新案例选择、双盲标注、两份receipt和预裁决agreement后，才重新判断是否允许运行HBM回归与Top-10复核。v1.3当前不能标记`frozen`。
