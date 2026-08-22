# Theme Chokepoint Root Cause Equivalence Contract RC-1

状态：`FROZEN_PRODUCT_DECISION`

版本：`RC-1`

产品负责人裁定日期：2026-08-18

## 1. 目的与适用范围

本合同定义 Theme Chokepoint Review Program 如何跨 Cycle 判断两个 finding 是否属于同一根因，以及何时允许触发 `BLOCKED_REPEATED_ROOT_CAUSE`。

本合同只约束 review-program root-cause adjudication、history derivation 和 terminal transition。它不修改评分语义、P0/P1 严重度、Golden、Holdout、盲标结果、已冻结 after-snapshot 或历史 finding 内容。

本合同不得用于改写 Cycle 1–3 历史 receipt。对既有 Cycle 3 finalization 的影响只能通过新的 append-only 独立复核和纠偏 receipt 表达。

## 2. 分层定义

- `symptom`：最终观察到的错误结果。
- `execution_path`：触发错误的具体入口和调用链。
- `causal_mechanism`：使反例可达的直接失效控制。
- `root_cause`：导致该控制必然失效的共同架构原因。
- `architecture_family`：多个不同根因共享的上位主题；仅属于同一 family 不足以判定同一根因。

诸如“调用方可控”“缺少独立性”“自我证明”“验证不充分”只能作为 architecture family 描述，不能单独建立 root-cause equivalence。

## 3. SAME_ROOT 五项合取条件

两个 finding 只有在以下五项全部为 `same` 时，才允许分类为 `SAME_ROOT`：

1. `violated_invariant` 相同：违反同一条不可变业务或信任规则。
2. `trust_boundary` 相同：writer、validator、reader、state-transition consumer 的权责边界相同。
3. `causal_mechanism` 相同：新反例利用同一个缺失控制，而不只是产生相似结果。
4. `architecture_remedy` 相同：一个共同的架构级修复能够关闭两个 finding；若必须实施两个彼此独立的控制，则不属于同一根因。
5. `counterfactual_explanation` 相同：能够证明上一轮修复未触及该根因，因此新反例仍因同一原因成立。

以上为 AND 条件。任一条件不是 `same`，不得输出 `SAME_ROOT`。

## 4. 分类空间

每一条跨 Cycle 等价性判断必须输出以下一种分类：

- `SAME_ROOT`：五项条件全部满足。
- `SAME_FAMILY_DIFFERENT_ROOT`：上位主题相关，但 trust boundary、causal mechanism 或 architecture remedy 不同。
- `NEW_ROOT`：新的独立错误机制。
- `DUPLICATE_SAME_CYCLE`：同一 Cycle 内同一根因的另一条执行路径；同一 Cycle 只计一次。
- `UNKNOWN`：公开证据不足，不能完成等价性判断。
- `CONFLICTED`：独立裁决证据或裁决结果相互冲突。

只有同时满足以下条件的映射才能进入连续根因历史：

```text
classification == SAME_ROOT
AND evidence_state == supported
```

`UNKNOWN` 和 `CONFLICTED` 保持 finding open，并阻止 GO，但不得自动计入连续根因或触发 G12。

## 5. 禁止依据

以下因素单独或组合出现，均不足以建立 `SAME_ROOT`：

- root-cause 名称或字符串相同；
- finding 严重度相同；
- 影响相同 state、score 或 Hard Gate；
- 位于同一文件、模块或 Stage；
- 都可以描述为调用方可控、自我证明、缺少独立性或验证不足；
- 都需要“加强验证”；
- 最终业务影响相同。

## 6. 连续性和计数

根因在某一 Cycle 记为 `PRESENT`，必须有绑定该 Cycle 精确 frozen after-snapshot 的有效最小反例。

连续三轮要求：

```text
Cycle n-2 = PRESENT
Cycle n-1 = PRESENT
Cycle n   = PRESENT
```

计数规则：

- 同一 Cycle 的多个 path-level finding 映射到同一 root 时只计一次。
- 中间 Cycle 为 `CLOSED_PROVEN` 时连续性中断。
- 后续若主张中间 closure 为 false closure，必须在中间 Cycle 的精确 frozen snapshot 上复现反例；否则不得追溯恢复连续性。
- 后续出现类似问题不能单独证明中间 Cycle 存在该根因。
- root 重命名、合并或拆分必须由独立 alias/equivalence receipt 表达，不得改写历史记录。
- 只要一个 supported root 满足连续三轮，G12 即可要求 `BLOCKED_REPEATED_ROOT_CAUSE`。

## 7. 角色隔离

跨 Cycle 的 root-cause equivalence 必须由专门的 `Root Cause Adjudicator` 裁决。

Root Cause Adjudicator 必须：

- 运行在独立 task/context；
- 与 Developer、Reviewer A、Fresh Challenger、Challenger Adjudicator 和 Exit Auditor 使用不同 agent/session identity；
- 可以读取各 Cycle sealed finding、最小反例、精确 snapshot binding 和 closure evidence；
- 第一遍不得读取 Developer 或主调度 Agent 建议的 root 映射、期望的 BLOCKED/CONTINUE 结果或未封存解释；
- 对五项合取条件逐项记录 input、comparison、evidence 和 result；
- 在完成逐项判断前不得读取或继承既有 recurrence conclusion。

Challenger Adjudicator 只拥有 Challenger finding 分类权。除非被单独创建为满足上述隔离要求的 Root Cause Adjudicator，否则无权裁决跨 Cycle recurrence history。

Developer 和主调度 Agent只能汇总 sealed 裁决，不能自行创建或修改 `[1,2,3]` 历史。

## 8. 必需裁决字段

每一条 equivalence edge 至少包含：

- `current_finding_id`、`current_cycle` 和 current receipt SHA-256；
- `prior_finding_id`、`prior_cycle` 和 prior receipt SHA-256；
- 两侧 after-snapshot ID 和 SHA-256；
- 五项合取条件的逐项判断及证据位置；
- `classification`；
- `evidence_state`：`supported | unknown | conflicted`；
- immutable `canonical_root_id`，仅当 supported `SAME_ROOT` 时生成或复用；
- Adjudicator agent/session identity；
- sealed/fresh-context/first-pass isolation receipt；
- proof limits。

一条 current finding 最多只能有一个 primary `canonical_root_id`。可以记录 related family，但 related family 不计 recurrence。

## 9. 机器执行合同

Review-program validator 必须：

1. 将 canonical program/protocol path、schema 和 SHA-256 绑定到 cycle-start receipt，禁止调用方任选 JSON 作为 `--program`。
2. 使用独立的 `--root-cause-adjudication` 输入，不得将 adjudication receipt 冒充 program。
3. 验证 Root Cause Adjudicator 的 identity、角色隔离、sealed/fresh-context 和 receipt bindings。
4. 从 sealed Cycle findings 和 supported equivalence edges 机械推导 `root_cause_history`；不得信任调用方直接填写的 `[1,2,3]`。
5. 验证 history 中每个 PRESENT 都绑定对应 Cycle 的精确 frozen snapshot。
6. 在 Cycle Decision 和 finalization 中绑定 root-cause adjudication SHA-256。
7. 对 program 替换、schema 混用、root label 改写、history 删除、伪造或未绑定 adjudication 一律 fail closed。

Fail closed 的结果是 `INVALID_REVIEW_PROGRAM_TRANSITION`、`BLOCKED_NEEDS_PRODUCT_DECISION` 或 `READY_FOR_EXTERNAL_REVIEW`，不得通过默认推断生成 `SAME_ROOT`。

## 10. 当前 Cycle 1–3 的产品初始路由

以下只是新独立复核的初始 routing hypothesis，不是最终 recurrence 裁决：

- caller-controlled ledger/repository/provider row：建议比较为 `SAME_ROOT`，候选历史 `[1,2,3]`。
- accounting `not / absent / ceased`：建议比较为 `SAME_ROOT`，候选历史 `[1,2,3]`。
- arbitrary scorer / no-op calculator / partial family recompute：建议比较为 `SAME_ROOT`，候选历史 `[1,2,3]`。
- shared facade / shared authenticated principal：建议比较为 `SAME_FAMILY_DIFFERENT_ROOT`；principal root 候选历史 `[2,3]`。
- orchestrator receipt atomicity / monitoring refresh atomicity：建议比较为 `SAME_FAMILY_DIFFERENT_ROOT`，除非独立证据证明两者缺少同一个共享 Unit-of-Work 控制；当前最多为 `[2,3]`。

Root Cause Adjudicator 第一遍不得看到本节的 routing hypothesis。本节只能在其第一遍 sealed 后用于差异分析。

## 11. 当前状态边界

RC-1 的冻结不自动撤销或确认 Cycle 3 `BLOCKED_REPEATED_ROOT_CAUSE`。在独立 Root Cause Adjudicator 和修复后的 machine validator 完成前，当前状态应表达为：

```text
BLOCKED_PENDING_INDEPENDENT_ROOT_CAUSE_ADJUDICATION
```

不得启动 Cycle 4、Holdout、部署或 GO，也不得改写既有 Cycle 1–3 receipts。
