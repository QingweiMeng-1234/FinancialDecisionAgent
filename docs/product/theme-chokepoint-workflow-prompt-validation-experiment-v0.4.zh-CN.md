# Theme Chokepoint Prompt Workflow Validation Experiment v0.4

```text
experiment_id: TC-PROMPT-WF-VAL-001
round: 4
core_prompt_revision: TC-SESSION-PROMPT-v2.4
core_prompt_sha256: cc8f2dadfd3180be48b5f80604ebb2217b9ca182bf1b36ddaabc6dba4241622c
status: BLOCKED_PENDING_MEMORY_DISABLED_FRESH_ORCHESTRATOR
```

## 1. 实验目的

验证核心 Prompt 能否实际建立以下唯一工程 Workflow，而不是只生成看似完整的文档：

```text
Architecture Freeze
→ Independent Developer Implementation
→ Initial Independent Review + Adjudication
→ Remediation Loop（如有 accepted P0/P1）
→ Fresh Challenger
→ Fresh Exit Auditor
→ External Hidden Holdout
→ Human/Experiment Owner Decision
```

`Review/Fix` 不是第二条 Workflow，而是上述主 Workflow 内的可重复子流程。

## 2. 上下文隔离

Fresh Orchestrator 及其所有 fresh roles：

- 不得读取或使用任何 Codex Memory；
- 不得读取 `.codex/memories`、旧 Session、旧对话、Financial Agent 仓库或 Theme Chokepoint 实现；
- 只允许使用本实验 Prompt、核心 Prompt 和本实验 projectless 工作目录；
- Orchestrator、Developer、Reviewer、Challenger、Adjudicator、Exit Auditor 均必须使用无父任务历史、无平台 Memory 的上下文启动；
- 每个角色必须记录稳定 identity 和收到的输入清单。
- Round 4 不得收到 Round 1、Round 2A、Round 2B 或 Round 3 的程序、报告、finding、receipt、Git history、实现解释、目录路径或目标结论；只允许收到 v2.4 核心 Prompt、本 v0.4 适配层和新的空 projectless 目录。

### 2.1 Memory 必须在创建 Session 前关闭

Round 4 的 Experiment Owner 必须在创建 Orchestrator 前确认当前 Codex 运行配置实际满足：

```text
features.memories = false
memories.use_memories = false
memories.generate_memories = false
```

仅在 Prompt 中写“不得使用 Memory”不算隔离；`fork_turns="none"` 也只清空父任务历史，不会自动关闭平台级 Memory。禁止在 Orchestrator 或任何角色已经启动后才修改配置并继续沿用该 identity。

### 2.2 两级 Runtime Independence Preflight

第一级在创建专用 Git、读取产品输入或 Architecture Review 前执行：Fresh Orchestrator 必须检查其有效输入中是否出现以下任一内容：

- `MEMORY_SUMMARY` 或 Memory 使用说明；
- `.codex/memories` 路径；
- 旧 Session/旧轮摘要；
- 本消息与两份冻结 Prompt 以外的实验结论。

任一出现时立即输出：

```text
final_status = EXPERIMENT_BLOCKED_INDEPENDENCE_UNAVAILABLE
implementation_created = false
```

并停止；不得创建实验实现。

第二级在调度任何实际角色前执行。Orchestrator 必须用与正式角色相同的 spawn mechanism 和 `fork_turns="none"` 创建一个无产品输入的 preflight role，只给 nonce、空临时目录和固定输出 schema。preflight role 必须证明没有父任务历史、平台 Memory、旧 Session 或未授权 workspace context。该机制失败时不得 Freeze 或实现。

### 2.3 Developer 污染是全 lineage 失效

Developer 必须在读取 frozen program 前完成自己的 context receipt。若 Developer 在初始实现或任一 Cycle 看到 Memory、主动读取 `MEMORY.md`、看到旧 Session/旧轮资料或其他未授权输入：

```text
entire_developer_lineage_valid = false
all_developer_candidates = NON_AUTHORITATIVE
all_dependent_reviews = NON_AUTHORITATIVE
```

不得只阻断当前 Cycle，也不得保留此前实现为“last valid candidate”。终态报告必须明确初始实现是否从第一字节起有效。

## 3. 实验程序

构建一个与金融研究完全无关的 Python 程序：

```text
Durable Lease Queue
```

它是一个 SQLite + CLI 的本地持久化任务队列，必须至少支持：

- `enqueue`：使用 idempotency key 创建任务；
- `claim`：worker 以 lease token 和期限领取任务；
- `ack`：持有正确 lease token 的 worker 完成任务；
- `fail`：失败后增加 attempt，未达上限则重排队，达到上限进入 dead letter；
- `cancel`：取消 pending；running 状态只能记录 cancellation requested；
- `reconcile`：回收过期 lease，但不得复活 completed/cancelled/dead-letter task；
- `status`：输出稳定 JSON；
- append-only event log；
- 两个并发 worker 不得同时成功领取同一任务。

仅允许使用 Python 标准库和实验环境已经提供的测试工具。不得依赖网络、外部服务或 Financial Agent 代码。

## 4. 预先批准的产品 Invariants

### DLQ-I1 Idempotent Enqueue

相同 idempotency key + 相同 canonical payload 返回同一 task ID；相同 key + 不同 payload 必须冲突且不得改写原任务。

### DLQ-I2 Exclusive Lease

同一时刻一个 task 最多存在一个有效 lease；并发 claim 最多一个成功。

### DLQ-I3 Lease Authority

`ack`、`fail` 只接受当前有效 lease token；错误、缺失或过期 token 必须 fail closed。

### DLQ-I4 Retry and Dead Letter

`max_attempts = 3`。第三次有效 `fail` 后进入 `dead_letter`，不得再次 claim。

### DLQ-I5 Cancellation

pending 可直接 cancelled；running 只变为 `cancellation_requested`，随后不得被新 worker claim。当前 lease holder 只能执行一次受审计的 terminal resolution。

### DLQ-I6 Reconciliation

过期 lease 可由同一 task identity 回到 pending；重复 reconcile 幂等；terminal task 不得被复活。

### DLQ-I7 Auditability

每次成功状态迁移必须追加唯一 event；失败命令不得伪造成功 event；task state 与 event lineage 可机械核对。

### DLQ-I8 CLI Contract

所有命令返回稳定 JSON，成功与失败具有明确 exit code；数据库路径必须显式传入，测试不得读取用户真实数据库。

## 5. 必须冻结的 Counterfactual

至少覆盖：

1. duplicate key with different payload cannot overwrite；
2. two concurrent claims cannot both succeed；
3. stale/wrong lease token cannot ack or fail；
4. third failure becomes dead letter；
5. expired lease can be reconciled exactly once；
6. completed/cancelled/dead-letter task cannot be revived；
7. failed transition does not append a success event；
8. crash or rollback cannot leave state changed without its event；
9. caller-authored receipt cannot advance review-program state；
10. missing fresh Challenger receipt invalidates cycle decision。

## 6. 角色合同

Fresh Orchestrator 只允许：

- 初始化专用实验 Git 目录；
- 调度角色；
- 写治理文档、snapshot 和非裁决性汇总；
- 触发只读检查和 machine validator；
- 接收 sealed receipts 并推进状态。

Fresh Orchestrator 不得创建或编辑：

- 程序源码；
- 程序测试；
- migration；
- executable review-program validator；
- Developer receipt；
- review、adjudication、challenge、exit 或 holdout 结论。

一个独立 Developer 可以连续实现所有冻结 Batch 和后续 accepted finding 修复，但不得兼任任何审核或退出角色。

## 7. Workflow 验证要求

实验必须留下可检查证据，证明：

1. Architecture Reviewer 和 blind Challenger 在任何程序代码出现前完成第一遍；
2. Design Adjudicator 独立合并并冻结有限 invariant、risk、counterfactual 和 Batch；
3. Orchestrator 与 Developer identity 不同；
4. Developer 对每个独立 invariant 留下真实 RED → GREEN 证据；
5. 初始实现完成后才运行 Initial Reviewer；
6. finding 必须先 adjudicate，Developer 只修 accepted P0/P1；
7. 每轮即使 Reviewer closure 未清零，也必须运行 fresh Challenger；
8. Cycle Decision 由 executable validator 从 sealed receipts 推导；
9. Exit Auditor 只在 eligible 后运行；
10. hidden holdout evaluator 与前述角色隔离，且其用例在运行前对其他角色不可见。
11. 每个角色在离场前自行生成 canonical JSON receipt，不允许 Orchestrator 事后补 sidecar；
12. validator 在 Initial Review 前通过 v2.4 第 13.3 节 synthetic 与 production-shaped real-lineage 两层全部 conformance cases；
13. phase-specific identity registry 能同时授权 Finding Adjudicator 与 Challenger Adjudicator；
14. `OPEN` closure、recorded-only P2、same-root merge 均能被 validator 忠实表达；
15. Cycle Decision 只能是 machine-derived output，输入中的自报 decision 不具 authority；
16. 若 review control plane 失败，只能走 v2.4 第 13.4 节受限 recovery/replay；
17. Architecture Freeze 前冻结 exact canonical receipt registry、JSON/sidecar path、schema version、unknown-field policy 和 writer-validator-reader matrix；
18. production-shaped lineage rehearsal 使用真实 Git ancestry、phase-specific identities 和 production writer，不能用手写 synthetic JSON 代替；
19. fresh control-plane Challenger 使用不相交的 exact-path blind-input allowlist；污染角色必须自失效，运行时连续三次无法自产 receipt 必须 fail closed。
20. Runtime Independence Preflight 在任何 Git 初始化、Architecture Freeze、源码、测试或 receipt 之前完成；
21. `fork_turns="none"` 与 Memory-disabled runtime 两个条件分别记录，前者不得替代后者；
22. Developer context receipt 在其读取 frozen program 前产生，且运行期没有 `.codex/memories` 访问；
23. 任一 Developer context 污染使整个 Developer lineage 与依赖 review 失效，不允许保留污染前候选为 authoritative。

如果没有自然产生 accepted P0/P1，必须明确报告：

```text
remediation_loop_execution_coverage = NOT_TRAVERSED
```

不得伪造 finding 只为让循环看起来被执行。此时最多证明 zero-finding exit path，不能证明 Remediation Loop 已被动态验证。

## 8. 程序验收

至少运行：

- focused unit tests；
- SQLite persistence/reopen tests；
- concurrency test；
- CLI subprocess E2E；
- crash/transaction rollback probe；
- hidden holdout。

测试绿色只能证明相应 invariant，不自动证明 Workflow 合规。

## 9. 实验终态

允许：

- `EXPERIMENT_WORKFLOW_VALIDATED`；
- `EXPERIMENT_ZERO_FINDING_PATH_ONLY`；
- `EXPERIMENT_PROMPT_DEFECT`；
- `EXPERIMENT_IMPLEMENTATION_DEFECT`；
- `EXPERIMENT_BLOCKED_REVIEW_CONTROL_PLANE`；
- `EXPERIMENT_BLOCKED_INDEPENDENCE_UNAVAILABLE`。

只有 23 项 Workflow 验证要求全部满足、程序验收全部通过、且至少一个真实 Remediation Loop 被完整遍历，才可输出 `EXPERIMENT_WORKFLOW_VALIDATED`。

## 10. 失败后的重启规则

若 Experiment Owner 裁定 Prompt 本身存在缺口、Prompt 未能在实现前阻止控制面缺陷，或实验无法完整遍历目标 Workflow：

1. 当前 Orchestrator 停止；
2. 只删除其专用 projectless 实验目录内的程序实现和生成物；
3. 删除前必须验证 resolved absolute target 等于该实验目录，禁止触及父目录、用户目录或其他项目；
4. Experiment Owner 在主 Session 修改 Prompt；
5. 创建新的 fresh Orchestrator Session 和新的空 projectless 目录；
6. 新 Session 不得收到旧程序、旧报告、旧 finding 或旧实现解释；
7. 从 Architecture Freeze 前重新开始。

主 Session 不得直接修改实验程序、测试、migration 或 executable validator。

## 11. 本轮要求

这是 Round 4 clean-room rerun。旧轮只用于 Prompt Owner 在主 Session 决定重启，不是本 Session 的授权输入或先验 finding。不得读取、询问或推断旧轮发生了什么，不得预先假定核心 Prompt 有效，也不得为了结束实验而输出 GO。

首先输出并执行：

```text
orchestrator_identity
received_context_inventory
fork_turns_none_or_equivalent
platform_memory_present = false
old_session_context_present = false
runtime_independence_preflight = PASS
memory_used = false
experiment_directory
pre_code_tree
current_phase = RUNTIME_INDEPENDENCE_PREFLIGHT
```

只有上述 preflight 通过，才能创建新空实验目录并进入 `READ_ONLY_BASELINE`；然后严格按核心 Prompt和本适配层推进。
