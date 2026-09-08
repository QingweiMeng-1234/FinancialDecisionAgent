# Theme Chokepoint Agent Execution Ablation API and Data Contract

## 文档控制

| 字段 | 值 |
| --- | --- |
| 状态 | `IMPLEMENTATION_DRAFT_ALIGNED_TO_FROZEN_PRD` |
| 版本 | 1.0 |
| 日期 | 2026-09-04 |
| 产品 | Financial Agent / Theme Chokepoint Research |
| API family | theme-chokepoint.v1 |
| Transport | Repo-native Python service with MCP and CLI adapters |
| Product contract | [Theme Chokepoint Agent Execution Ablation PRD](../product/theme-chokepoint-agent-execution-ablation-prd.md) |
| System design | [Theme Chokepoint Agent Execution Ablation System Design](../architecture/theme-chokepoint-agent-execution-ablation-system-design.md) |
| Freeze manifest | [Theme Chokepoint Agent Execution Ablation Freeze Manifest](../product/theme-chokepoint-agent-execution-ablation-freeze-manifest-v1.0.json) |

本文件与冻结 PRD 对齐，是实现合同草案；不声明计划接口已注册、部署或连接 live provider。

## 1. 目的与证明边界

本合同定义 Theme Chokepoint 运行、Stage 3 single/isolated-workers 对照实验、持久化研究产品及其审计边界。它不定义必须存在的 HTTP 资源模型，也不证明生产部署、外部 provider、凭证或模型事实准确率。shadow execution 不得改变 canonical Stage3Result、production RunStatus 或交易/量化消费。

## 2. Surface 与实现状态

### 2.1 当前已实现 legacy adapter surface

[`src/event_collector/theme_chokepoint/interfaces.py`](../../src/event_collector/theme_chokepoint/interfaces.py) 当前提供：

| Handler name | 当前领域调用 |
| --- | --- |
| theme_chokepoint_get_run | get_run_status(run_id)，返回 run_id/status/next_stage |
| theme_chokepoint_finalize | Stage 5 finalize，返回 JSON-able ArtifactManifest |
| theme_chokepoint_compare_runs | compare_runs，返回 RunComparison |
| theme_chokepoint_record_feedback | 保存 FeedbackCorrection |

`interfaces.py` 已提供 FastMCP-compatible `register_mcp_tools()` helper，但仓库当前没有证据表明主 `FinancialAgentMCP` 默认调用了该 helper；因此“handler 已实现”不等于“已部署或默认可发现”。CLI 当前只有 status、finalize、compare、feedback。legacy handlers 目前不使用本文件的统一 envelope；兼容窗口内不得悄然包裹或重命名其返回值。

### 2.2 计划 research tools（未实现声明）

需另行实现并通过 contract tests 后才能标记 available：

- theme_chokepoint_start_run_v1
- theme_chokepoint_confirm_anchors_v1
- theme_chokepoint_continue_run_v1
- theme_chokepoint_get_run_v1
- theme_chokepoint_finalize_v1
- theme_chokepoint_compare_runs_v1
- theme_chokepoint_record_feedback_v1

### 2.3 默认关闭的 ablation admin tools

只允许测试、评估和显式 shadow 环境：

- theme_chokepoint_start_ablation_v1
- theme_chokepoint_get_execution_v1
- theme_chokepoint_retry_task_v1
- theme_chokepoint_get_pair_report_v1
- theme_chokepoint_cancel_execution_v1（可选）

## 3. 通用协议

计划 v1 tools 使用 common envelope：

    {
      "api_version": "theme-chokepoint.v1",
      "request_id": "req_01J...",
      "status": "success",
      "data": {},
      "warnings": [],
      "error": null
    }

API status 为 success、degraded 或 error，与 domain RunStatus 分离。允许部分 task 完成时必须用 degraded 并列出不完整原因。error 包含 code、message、retryable、details。

序列化规则：

- JSON 字段使用 snake_case；date 为 YYYY-MM-DD；datetime 为 RFC 3339 UTC Z。
- ID 为 opaque string；可选值为 null，集合为 []。
- SHA-256 为小写 64 字符十六进制。
- tuple 序列化为数组；数组按 planner stable order，而非 completion order。
- 为兼容当前 `ResearchRequest`，`run_id` 由 start-run 调用方提供但必须满足 opaque-ID policy，且不得覆盖既有 run；execution/pair/task/attempt/receipt/evidence/source ID 均由服务端生成。任何调用方都不能覆盖服务端状态、成本、模型 identity、contract hash 或 manifest。
- 本地路径、SQL、credential 和 secret-bearing headers 不得外泄。

## 4. Research API

### 4.1 Start run

请求 data 是 ResearchRequest 的 JSON 表示：

    {
      "run_id": "run_01J...",
      "theme": "advanced packaging glass substrate",
      "trigger": "capacity constraint review",
      "region": "global",
      "as_of_date": "2026-09-04",
      "time_horizon_months": 12,
      "analysis_goal": "assess whether supply is a chokepoint",
      "seed_products": ["qualified glass substrate"],
      "seed_companies": [],
      "research_mode": "theme_chokepoint",
      "max_depth": 3,
      "max_nodes": 30,
      "max_iterations": 5,
      "max_sources": 100,
      "max_time_seconds": 900,
      "max_cost_usd": 20.0,
      "max_product_anchors": 3
    }

响应 data 是 Stage 1 snapshot 或 Stage1To7RunManifest。范围不清晰返回 NEEDS_CLARIFICATION；可清晰但等待确认返回 AWAITING_PRODUCT_CONFIRMATION。

### 4.2 Confirm anchors

请求包含 run_id、anchor_ids、confirmed_by；返回 ConfirmationReceipt。没有 durable confirmation receipt 时 Stage 2 必须拒绝推进。

### 4.3 Other research tools

continue 返回 Stage1To7RunManifest；get 返回 run_id/status/next_stage；finalize 返回 ArtifactManifest；compare 返回 RunComparison；record_feedback 接收 FeedbackCorrectionDraft 并返回 FeedbackCorrection。

## 5. Execution Ablation API

### 5.1 Start

`theme_chokepoint_start_ablation_v1` 每次创建一个 execution arm。创建第一臂时省略 `pair_id`，服务端冻结 Stage 2 输入并返回新 `pair_id`；创建第二臂时携带该 `pair_id`，服务端必须验证 mode 尚未存在且所有 fairness identity 与第一臂完全一致。重复 `idempotency_key` 返回原对象，不得创建第三臂。

请求 `data`：

```json
{
  "base_run_id": "run_01J...",
  "execution_mode": "single",
  "pair_id": null,
  "plan_profile": "strict_replay",
  "max_concurrency": 1,
  "shadow": true,
  "expected_input_snapshot_sha256": "<64-lowercase-hex>",
  "expected_executable_contract_id": "server-declared-contract-id",
  "expected_executable_contract_sha256": "<64-lowercase-hex>",
  "idempotency_key": "caller-stable-opaque-key"
}
```

约束：

- `base_run_id` 必须存在、处于 `SUPPLY_CHAIN_GRAPH_READY`，且尚无冲突的 canonical Stage 3 结果；
- `single` 强制有效并发为 1；`isolated_workers` 的 `max_concurrency` 由服务端 clamp/fail-closed policy 决定；
- Phase A/B 只接受 `strict_replay`；`dynamic_shadow` 只允许显式 shadow 权限，且后续 plan divergence 会使 topology-only 比较无效；
- 客户端不能指定 model ID、prompt version、provider endpoint/credential、source policy、评分 hash、治理 bundle 或本地路径；这些由冻结服务器配置写入 pair manifest；
- start 只创建 candidate execution，不创建或覆盖 canonical `Stage3Result`，也不推进 production `RunStatus`。

响应 `data`：

```json
{
  "execution_id": "exec_01J...",
  "base_run_id": "run_01J...",
  "pair_id": "pair_01J...",
  "execution_mode": "single",
  "plan_profile": "strict_replay",
  "execution_status": "pending",
  "effective_max_concurrency": 1,
  "input_snapshot_sha256": "<64-lowercase-hex>",
  "plan_hash": "<64-lowercase-hex>",
  "task_counts": {
    "planned": 18,
    "pending": 18,
    "running": 0,
    "succeeded": 0,
    "failed_retryable": 0,
    "failed_terminal": 0,
    "exhausted": 0
  }
}
```

### 5.2 Pair identity

Pair 必须绑定：

```text
pair_id
base_run_id
frozen_stage2_snapshot_sha256
single_execution_id
isolated_execution_id
fairness_identity_hash
plan_profile
ordered_task_key_set_sha256
```

两条 shadow arm 的 output 只能进入各自 execution candidate namespace，不能写入或覆盖 `repository.get_stage3_result(base_run_id)` 对应的 canonical Stage3Result。只有经过 pair validity gate 的比较报告才可声明有效对照。

### 5.3 Task identity（重要）

`task_key` 表示配对两臂共享的逻辑任务，不包含 run、pair 或 execution identity：

```text
task_key = SHA-256(protocol_version, input_snapshot_sha256, iteration,
                  node_id, material_field_or_route, scope_hash,
                  sealed_query, source_policy_id, task_budget,
                  applicable_contract_versions)
task_id  = SHA-256(execution_id, task_key)
```

canonical serialization 必须固定字段名、UTF-8、key order、number/date normalization，并由实现测试锁定。`base_run_id`、`pair_id`、`execution_id`、`execution_mode`、worker/attempt identity 均不得混入 `task_key`。因此 single 与 isolated-workers 必须拥有相同 task_key，但不同 task_id。同一 execution 内重复提交相同 task_key 不得创建第二个 logical task。

`task_budget` 是 canonicalized 配置上限 tuple，不是 reservation、实际 token/cost、provider billing 或 elapsed time。

任务状态合同只有 pending、running、succeeded、failed_retryable、failed_terminal、exhausted；不定义 CLAIMED 状态。lease ownership、attempt 和 fencing token 是 receipt/运行元数据，不是新的可见任务状态。

### 5.4 Execution query

`theme_chokepoint_get_execution_v1` 请求：

```json
{
  "execution_id": "exec_01J...",
  "include_tasks": true,
  "include_receipts": false,
  "task_cursor": null,
  "task_limit": 100
}
```

响应返回 execution identity/status、budget usage、task counts、candidate result reference，以及按 planner `task_key` 稳定顺序分页的 `TaskRecordSummary`。其中 `status` 可为任一 task lifecycle 状态，`outcome` 在 task 未终态时为 null；`EvidenceTaskOutcome` 只表示终态结果。receipt 摘要默认只返回 ID、状态、时间、usage 与稳定 error code；raw prompt、credential、provider secret 和任意本地路径永不返回。

### 5.5 Task retry

`theme_chokepoint_retry_task_v1` 请求：

```json
{
  "execution_id": "exec_01J...",
  "task_id": "<execution-specific-task-id>",
  "reason": "operator-approved retry after provider timeout",
  "idempotency_key": "caller-stable-opaque-key"
}
```

只接受 `failed_retryable`，沿用原 `task_key`/`task_id` 并生成新的 `attempt_id`。服务端原子校验当前状态、lease/fencing 和剩余 shared budget；客户端不能提交或覆盖 fencing token。`succeeded`、`failed_terminal`、`exhausted` task 返回 `RETRY_NOT_ALLOWED`。重复幂等键返回同一 attempt，不得重复计费。

### 5.6 Pair report

`theme_chokepoint_get_pair_report_v1` 请求包含 `pair_id` 和可选 `include_task_diff`。响应返回 pair_manifest、single_summary、isolated_workers_summary、task_diff、quality_gates、cost_latency_metrics、validity、quality_conclusion、operational_conclusion、deployment_recommendation、decision_policy_id 和 proof_limitations。

`quality_conclusion` 固定为 `not_evaluated | insufficient_evidence | equivalent_within_threshold | single_better | isolated_workers_better | mixed`。`deployment_recommendation` 固定为 `none | retain_single | consider_isolated_workers`，只有显式版本化 evaluation policy 才能生成非 `none` 值；它绝不触发自动 promotion。报告必须区分 `packet_blind` 与 `context_blind`：两臂 packet 都应通过禁止字段检查，single shared-session arm 不得被标成 context-blind。scope、source snapshot、model/prompt、schema、scoring/governance contract、plan profile、task-key set/order 或配置预算上限任一不一致，必须 `validity=invalid`、错误码 `PAIR_INVALID`，且结论为 `insufficient_evidence`、recommendation 为 `none`。两臂未终态时返回 `status=degraded`、`pair_status=incomplete`，不得提前生成偏好结论。

### 5.7 Cancel（可选、若实现）

`theme_chokepoint_cancel_execution_v1` 只能阻止未开始的新 attempt，并把未运行工作归类为取消原因；它不能删除 receipt、provider lineage 或已完成 candidate artifact，也不能改变 canonical run。取消后的精确 task 状态映射必须在实现 ADR 中定义；在该映射冻结前，本 tool 保持不可用。

## 6. Internal Python/data contracts

领域对象继续复用 src/event_collector/theme_chokepoint/contracts.py：ResearchRequest、DemandFrame、ProductAnchor、SupplyChainGraph、Stage3Result、Stage4Result、ArtifactManifest、FeedbackCorrection 和 RunComparison。

计划 execution DTO：

- EvidenceTask：task_key、task_id、pair_id、base_run_id、execution_id、base scope、iteration、node/material field、query、policy ID、input snapshot hash 和 task budget。
- TaskRecordSummary：execution/task identity、任一 task lifecycle status、nullable outcome、当前 attempt/lease 摘要和稳定 planner position。
- EvidenceTaskOutcome：task identity、仅终态、candidate IDs、provider receipt IDs、token/cost/elapsed 和稳定 error code。
- BlindCounterTask：base_run_id、execution_id、task_key、task_id、可选 pair_id、node/region/as-of/horizon、客观问题、source policy、demand/supply/alternatives routes、dedupe identity 和 budget；禁止 provisional score、candidate conclusion、main summary 和 confidence。
- AgentExecutionReceipt：receipt/task/attempt identity、execution mode、worker_role、worker_id、`packet_blind`、`context_blind`、model/prompt、input hash、context size/hash、时间、provider/output IDs、status/error 和 cost/token usage。`worker_id` 不等于 arm-level `execution_id` 或 retry-level `attempt_id`；single 可复用一个 worker ID，isolated 必须保留可证明的上下文隔离。两种模式的 counter packet 都应为 blind；复用 primary session 的 single counter 必须记录 `context_blind=false`。
- PairManifest：pair/base run、两条 execution IDs、共享 scope/source/model/contract/budget hashes、validity 和 report references。

所有 execution 产物必须使用独立 candidate namespace；canonical Stage 3 repository writes 只由 deterministic promotion/production Stage 3 owner 执行。

candidate、evidence card、claim 和 source snapshot 的评估结果也必须写入 candidate namespace（至少包含 pair_id/execution_id）。Ablation 结果不会自动 promotion；只有显式、经过授权、candidate hash/contract/输入状态 gate 的 promotion 操作，才能写入仍处于 `SUPPLY_CHAIN_GRAPH_READY` 且与冻结快照一致的 target run，或写入明确创建的新 canonical revision。它必须保留来源 execution ID、pair manifest 和 reconciliation receipt，且不得覆盖已有 canonical Stage3Result。

### 6.1 Internal promotion contract（首期不暴露为 MCP/CLI tool）

```json
{
  "source_execution_id": "exec_01J...",
  "candidate_result_sha256": "<64-lowercase-hex>",
  "target_run_id": "run_01J...",
  "expected_target_status": "SUPPLY_CHAIN_GRAPH_READY",
  "expected_frozen_stage2_snapshot_sha256": "<64-lowercase-hex>",
  "expected_stage3_absent": true,
  "reason": "explicitly approved promotion",
  "idempotency_key": "caller-stable-opaque-key"
}
```

调用主体和 authorization policy ID 必须来自经过认证的 adapter context，不能相信 body 中自报的 actor。Promotion service 重新验证 candidate/result hash、source lineage、合同、目标快照和 target status，并用 compare-and-set 保证 Stage 3 尚不存在。成功结果包含 `promotion_id`、source execution/pair ID、target run ID、canonical result hash/revision、stage receipt ID、new RunStatus、authorized actor/policy 和时间。任何冲突返回 `PROMOTION_CONFLICT` 或 `CANONICAL_RESULT_WRITE_FORBIDDEN`，不得部分写入；重复 idempotency key 返回同一 promotion receipt。

WP0–WP4 不注册 promotion tool。未来若增加外部 adapter，必须单独完成授权、审计和 contract review；pair report 的任何结论字段都不能触发该 service。

## 7. 状态与错误

Domain RunStatus 原样保留：

REQUEST_STORED、NEEDS_CLARIFICATION、AWAITING_PRODUCT_CONFIRMATION、READY_FOR_SUPPLY_CHAIN、SUPPLY_CHAIN_GRAPH_READY、CHOKEPOINT_ASSESSMENT_READY、INCOMPLETE_BUDGET_EXHAUSTED、COMPANY_ASSESSMENT_INCOMPLETE、COMPANY_ASSESSMENT_READY、PERSISTENT_RESEARCH_READY、MONITORING_READY、SIGNAL_EXPORT_READY。

Task status：pending、running、succeeded、failed_retryable、failed_terminal、exhausted。领取任务时在一次原子操作中由 pending/允许重试的 failed_retryable 进入 running 并创建 lease；`CLAIMED` 不是公开状态。迟到的 fenced attempt 可记录为 orphaned receipt metadata，但不改变 task status。

Execution status：pending、running、succeeded、degraded、failed、exhausted、cancelled。它描述整个 execution candidate，不替代 task status 或 Domain RunStatus。

Pair status：created、running、completed、incomplete、invalid、cancelled。

稳定错误码：

| 类别 | Codes |
| --- | --- |
| Request/run | `RUN_NOT_FOUND`, `INVALID_REQUEST`, `INVALID_STATE_TRANSITION`, `CLARIFICATION_REQUIRED`, `PRODUCT_CONFIRMATION_REQUIRED`, `CONTRACT_MISMATCH` |
| Execution/task | `EXECUTION_NOT_FOUND`, `EXECUTION_MODE_NOT_ALLOWED`, `TASK_NOT_FOUND`, `TASK_LEASE_CONFLICT`, `LEASE_FENCED`, `RETRY_NOT_ALLOWED`, `BUDGET_EXHAUSTED`, `IDEMPOTENCY_CONFLICT` |
| Provider/model | `PROVIDER_UNAVAILABLE`, `PROVIDER_TIMEOUT`, `PROVIDER_TRANSPORT_ERROR`, `PROVIDER_BAD_RESPONSE`, `MODEL_TIMEOUT` |
| Evidence/contract | `SCHEMA_VALIDATION_FAILED`, `EVIDENCE_ID_OUT_OF_SCOPE`, `SOURCE_DATE_INVALID`, `SOURCE_STALE`, `RECONCILIATION_FAILED`, `ARTIFACT_MISSING` |
| Pair/promotion | `PAIR_INVALID`, `TASK_PLAN_DIVERGED`, `PROMOTION_NOT_AUTHORIZED`, `PROMOTION_CONFLICT`, `CANONICAL_RESULT_WRITE_FORBIDDEN` |
| Product/general | `FEEDBACK_INVALID`, `INTERNAL_ERROR` |

## 8. 幂等、并发、恢复与安全

- run/execution/task/pair 使用 opaque IDs 和内容/hash identity；重复请求返回既有 logical object 或明确冲突，不重复 materialize 或计费。
- lease/fencing 防止迟到 worker 覆盖新 owner；失败 task 定向恢复，成功 evidence/receipt 不删除。
- 每个 execution 拥有一份 source/query/time/cost/token 总预算，pair gate 要求两臂预算上限相同；multi 只允许 server-bounded concurrency，按 planner 的 task_key order merge。
- provider request、raw response、parsed result、reconciliation 沿用 repository 的 lineage pattern。
- ablation tools 默认 disabled；需显式测试/shadow 权限。调用方不能选择任意 credential、provider endpoint、model secret/path。
- Blind counter payload 由 pre-task gate 扫描禁止字段；receipt 不保存密钥。
- shadow 结果不得进入 canonical run status、signals 或交易消费；canonical write 被拒绝时使用 CANONICAL_RESULT_WRITE_FORBIDDEN。

## 9. 兼容性与测试证明边界

legacy unversioned tools 保持原字段与语义；新能力使用 _v1 tool names 和 theme-chokepoint.v1 envelope。新增可选字段向前兼容；改 envelope/status/删除字段须 v2 或显式迁移。新 task/receipt 表必须 forward-compatible migration。execution mode 不改变历史运行解释，关闭 multi 不删除 evidence/receipt。

必须测试 stable task planning、两臂 task_key 对齐、task_id 派生、bounded fan-out、stable merge、shared budget、blind packet、packet/context blindness 区分、isolated worker identity、failure containment、lease/fencing、targeted retry、provider lineage、pair validity、canonical write isolation 和 legacy regression。测试不证明 live provider、部署、真实成本或研究质量；质量结论只能来自 frozen-corpus paired evaluation 与独立 holdout。

## 10. 当前实现状态

仓库已有 Stage 1–7 domain contracts、repository lineage、Stage 5 artifact service、Stage 6/7 orchestrator adapters 和 legacy interface。当前 `EvidenceChokepointLoop.run()` 仍直接调用 `ThemeChokepointRepository.save_stage3_result()`；该方法要求 `SUPPLY_CHAIN_GRAPH_READY`、写入 canonical Stage 3 并更新 run status。PRD 要求的 task planner、framework-neutral executors、candidate namespace/ledger、promotion service、blind counter adapter、pair report 和 versioned tools 尚未实现。默认生产行为仍是现有顺序流程；新 execution tools 在实现、测试和授权前不可用。
