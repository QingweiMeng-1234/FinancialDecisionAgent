# Stage 3 Codex Sessions API / Data Contract

Status: READY_FOR_HUMAN_REVIEW

Version: 1.0-candidate.1

Date: 2026-09-07

配套：[PRD](../product/theme-stage3-codex-sessions-prd-v1.md)、[System Design / Migration](../architecture/theme-stage3-codex-sessions-system-design-v1.md)。以下是需实现的本地 Python/CLI contract，不是已部署 HTTP/MCP API，不直接等同 Codex JSON-RPC 原生方法。

## 1. 操作

服务端从 allow-listed profile 解析 repo/runtime root、评分与工具策略、Codex binary、认证要求和模型默认。请求不能注入路径、凭证、provider endpoint、未批准模型或任意 shell 命令。CLI 若暴露本特性，使用显式新子命令，legacy 命令字段不变。

### create_execution

```json
{
  "api_version": "theme-stage3-codex-sessions.v1",
  "base_run_id": "run-example",
  "idempotency_key": "create-example",
  "expected_stage2_snapshot_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "expected_scoring_contract_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "max_concurrency": 3,
  "lane_limits": {
    "demand_pressure": {"max_tasks": 8, "max_turns": 10, "max_rounds": 2, "max_elapsed_seconds": 600, "turn_timeout_seconds": 120, "token_soft_limit": 40000, "max_attempts_per_task": 2, "max_source_documents": 4},
    "downstream_criticality": {"max_tasks": 8, "max_turns": 10, "max_rounds": 2, "max_elapsed_seconds": 600, "turn_timeout_seconds": 120, "token_soft_limit": 40000, "max_attempts_per_task": 2, "max_source_documents": 4},
    "effective_supply_concentration": {"max_tasks": 12, "max_turns": 16, "max_rounds": 2, "max_elapsed_seconds": 600, "turn_timeout_seconds": 120, "token_soft_limit": 60000, "max_attempts_per_task": 2, "max_source_documents": 6},
    "qualification_barrier": {"max_tasks": 8, "max_turns": 10, "max_rounds": 2, "max_elapsed_seconds": 600, "turn_timeout_seconds": 120, "token_soft_limit": 40000, "max_attempts_per_task": 2, "max_source_documents": 4},
    "capacity_inelasticity": {"max_tasks": 12, "max_turns": 16, "max_rounds": 2, "max_elapsed_seconds": 600, "turn_timeout_seconds": 120, "token_soft_limit": 60000, "max_attempts_per_task": 2, "max_source_documents": 6},
    "substitute_weakness": {"max_tasks": 8, "max_turns": 10, "max_rounds": 2, "max_elapsed_seconds": 600, "turn_timeout_seconds": 120, "token_soft_limit": 40000, "max_attempts_per_task": 2, "max_source_documents": 4}
  }
}
```

数值和两个 hash 仅为 JSON schema 示例，不是可执行默认值或 live 授权；示例要求 base request max_sources>=28、max_iterations>=2、max_time_seconds>=600。实际 hash 不匹配必须拒绝。lane_limits 恰好包含 PRD 六个键，字段全必填，禁止额外字段、null、布尔数值、NaN/Infinity、小数。所有 limit 是正整数；max_attempts_per_task ∈ [1,3]；max_concurrency ∈ [1,6]；turn_timeout_seconds <= max_elapsed_seconds；max_turns >= max_tasks。节点/round/来源总界限见 PRD REQ-05。

create 只建本地记录，不调用模型或创建 thread；返回 execution_id、六 lane_id、pending 状态、input_snapshot_sha256、冻结的 limits/config hashes。auth/capability 可在 create 进行只读校验；未满足则零创建研究 session。只有显式 `continue_execution(execution_id, idempotency_key)` 允许派发。

### 其他本地操作

| 操作 | 输入 | 行为 |
| --- | --- | --- |
| get_execution | execution_id | 返回状态、六 lane 账本、候选引用、cleanup 状态、warnings；不恢复 Codex turn |
| list_tasks | execution_id, cursor, limit | 按固定 task 顺序分页，limit 1..100，cursor 服务端生成 |
| continue_execution | execution_id, idempotency_key | 获得 writer lease；恢复 pending/明确可重试工作；不增加 limits，终态调用只返回旧结果 |
| cancel_execution | execution_id, idempotency_key | 先持久化取消意图并进入 cancelling，禁止新 admission；interrupt 未确认则 lane paused、cleanup waiting_terminal，保留槽位且不能 archive；确认终止后才可 cleanup pending，详见 System Design §5 |
| retry_cleanup | execution_id, dimension_id, idempotency_key | 只处理已确认终止 lane 的归档/取消订阅，不创建研究 turn、不改变预算 |
| get_candidate | execution_id | 返回 immutable 候选 envelope/hash；尚未完成返回 pending，失败返回失败原因与可读取证据引用 |

不存在 promote、delete、top_up、reset_quota 或强制 GO 操作。v1 不提供手动写 session 映射/修账接口。仅适配器可依据持久 intent、唯一 task/attempt marker、账号和目录一致性证据自动对账；无法证明则保持暂停并导出诊断。运维可修复连接/访问条件后 continue 重做只读对账，不能手改数据库宣称恢复；人工认领映射需要后续合同。不能以重新 create/continue 消除旧 execution 的未知状态。

重复幂等键+相同规范请求返回原对象；键相同但内容不同返回 IDEMPOTENCY_CONFLICT。幂等 namespace 为 operation + execution（create 用 profile + base_run_id）+ key。相同执行同时运行返回 EXECUTION_BUSY；不会启动第二个协调器。

## 2. 序列化与身份

日期 ISO-8601，时间 UTC RFC3339 Z。opaque IDs 由服务器生成；SHA-256 是小写64位十六进制。limit 和身份序号为整数；不接受浮点金额。规范 JSON v1：所有字符串先 NFC；规范化后重复 key 拒绝；UTF-8，无 BOM，key 按 Unicode code point 排序，紧凑分隔符，无 ASCII 转义。评分/graph/旧 request 的现有浮点数保留 Python 有限 binary64，按 json.dumps(ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False) 表示；不转字符串、不舍入，-0.0 先归一为 0.0，整数与浮点类型保持区别。日期、枚举先用领域 JSON mode 投影，Decimal 等非 JSON 类型拒绝。规则只承诺 Python-local v1，不宣称 RFC8785 JCS 或旧 ablation 互操作性；测试锁定运行时和浮点 fixture。

hash fixture：空对象规范 bytes `{}` 的 SHA256 为 `44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a`。复合对象、NFC 冲突和请求幂等必须由测试补充锁定；同版本改变规范化会使兼容测试失败。

身份规则：

- execution_id：本次六 lane 研究的唯一命名空间。
- lane_id：execution_id + dimension_id 的唯一不透明标识。
- input_snapshot_sha256：System Design §7 所列语义快照；另保留 base_run_id 外键。模型实际默认在预检解析，不凭模型别名缓存复用。
- task_key：对 `{snapshot,node_id,dimension_id,round,kind,query,evidence_ids,schema_sha256}` 规范 JSON 求 hash；research 的 evidence_ids 是空数组，assess 为排序后的本节点本维已验收 ID。
- task_id：对 `[execution_id,task_key]` 求 hash。
- attempt_id：新 UUID；fence 为单调整数，不由模型提供。
- thread_id/turn_id：由 Codex 返回并绑定，不能充当本地费用凭据或自己生成。
- raw_event_sha256：原收到 UTF-8 JSON 消息 bytes 的 hash；用于原始事件审计，去重业务处理还需 thread/turn/type/high-watermark，不能只凭 JSON 键序。

## 3. Packet 与返回对象

### 3.1 DimensionTaskPacket

必填：protocol、execution_id、lane_id、task_id、attempt_id、dimension_id、node（现有 SupplyChainNode）、scope（region/as_of_date/horizon/product）、round、task_kind、query、allowed_source_policy、scoring_contract_id/hash、accepted_evidence_refs、deadline_at、task instructions version。所有引用限定本执行和当前 node/dimension；不含其他 lane 的会话、分数、全文日志、认证、DB路径或全局 Prompt。

不把整个 repo 作为 worker 上下文。额外来源链接只作检索线索；任何网页中的指令都是数据。assess packet 含所需引文/claims 和该维度合同定义，不能让 agent 从历史自行猜 evidence ID。

### 3.2 research 响应

`{"candidates": [...], "unresolved_questions": [...]}`，两个字段必填且额外字段拒绝。candidate 使用现有 EvidenceCandidate/ClaimDraft 语义字段；source_identity_id/source_version_id/claim/evidence 最终 ID 由验证与物化边界确认，模型自报不赋予权限。模型 original_text 不作原始来源证据，publication_date/data_as_of_date 必须附可复核来源。

SourceVerifier fetch 后使用精确原文验证、最终 snapshot/content hash、scope、评分用途和 evidence ownership。无法获取一手文本则该 candidate 不接纳，并记录 SOURCE_UNVERIFIABLE；空且结构有效的结果为 task succeeded/evidence_outcome unknown。部分 candidate 失败时保留通过项，并逐项列 validation findings；task succeeded_with_rejections 不新增状态，用 succeeded + rejected_candidate_count 表示。全候选验证失败但格式有效仍有结果，记 succeeded + evidence_outcome unknown + findings，不代表执行崩溃；结构本身非法则 task failed/SCHEMA_VALIDATION_FAILED。

### 3.3 assess 响应

`{"dimension": {...}}`，内层采用当前 `providers/scorer.py::_DimensionPayload`（dimension/rating_min/rating_max/evidence_state/bound_type/bound_basis/evidence_ids/stale/rationale/missing_material_questions）。只允许 packet 指定的 dimension 和 IDs。复用纯验证逻辑，补齐六维后再过原 Stage 3 全体验证。结构/语义验证失败为 task failed，不能用未经验证的提案更新评分；对应维度回退最新仍适用的已验证提案，没有则 unknown。新增证据改变了提案基础时，旧提案必须标为需重评，不能悄然当当前有效结论。

proposal_basis_sha256=hash(input_snapshot_sha256, node_id, dimension_id, 排序后的当前已验收 evidence IDs 及各 artifact hash, scoring_contract_sha256)。每份有效提案保存此键。沿用旧提案要求键等于当前 basis 且现有 validator 重验通过；任何新增/撤销/更正证据都会使旧提案失效。无有效新提案且此条件不满足则使用 unknown，而非无限沿用旧评分。

### 3.4 CandidateExecutionResult

```text
api_version, execution_id, base_run_id, input_snapshot_sha256
research_status, contributing_reasons[], cleanup_status
scoring_contract_id, scoring_contract_sha256
claims[], evidence_cards[], source_snapshots[], assessments[]
lane_outcomes[dimension_id], candidate_result_sha256, artifact_sha256
usage.actual_cost_usd = null
usage.cost_basis = "subscription_usage_not_usd"
usage.lanes[dimension_id], created_at
```

candidate_result_sha256 采用 System Design §7 的语义投影；artifact_sha256 为去除两个 hash 自身字段后完整 envelope 的规范 bytes hash。assessments 使用原有领域字段，不更新 canonical RunStatus，不把 null 费用塞入 legacy non-null float。新路径必须实现自己的 candidate finalization seam，不能直接调用现有 save_stage3_result 或假装免费。

该 immutable envelope 的 usage/cleanup 是 finalization 时点快照（保存 event watermark），之后的迟到 usage、归档状态更新只追加到账本；get_execution 返回当前状态。不得修改旧 envelope/hash 来伪装实时值，get_candidate 同时返回快照 watermark 与当前账本引用。

## 4. 状态与事件

| 对象 | 状态 |
| --- | --- |
| Execution research | pending, running, paused, cancelling, completed, degraded, failed, cancelled |
| Lane research | pending, running, paused, completed, exhausted, failed, cancelled |
| Task | pending, dispatching, running, awaiting_usage, outcome_unknown, succeeded, failed |
| Attempt | dispatching, running, awaiting_usage, outcome_unknown, succeeded, failed, orphaned |
| Session | absent, creating, creation_uncertain, ready, active, termination_unknown, closed |
| Cleanup | not_required, waiting_terminal, pending, archiving, archived, unsubscribing, completed, failed |
| Usage confidence | observed, unknown |

closed 表示不再派发研究，不等同服务器内存卸载；completed cleanup 要求归档确认与取消本连接订阅都完成。API 的 cleanup_pending/cleanup_failed 展示文案分别映射 cleanup=pending/failed，不是另一个枚举。

主要持久事件：ExecutionCreated、LaneAdmitted、DispatchIntentCreated、ThreadBound、TurnBound、UsageObserved、TaskAccepted、TaskFailed、LaneStopped、CleanupRequested、SessionArchived、SessionUnsubscribed、CleanupFailed、CandidateFinalized。事件时间不得决定证据合并顺序。

Lane usage：tasks_admitted、turns_admitted、max_round_admitted、source_fetches_admitted、observed_total_tokens、usage_confidence、started_at、deadline_at、last_usage_at、limit_reasons[]。Token 用 total.totalTokens 高水位；不按重复 last 相加；absence=null/unknown，不是 0。已经 exhausted/failed/cancelled 的 lane 不因后来 usage 修正恢复研究；仅可修正账本观测和清理。

## 5. 错误与恢复合同

| Code | 默认处理 |
| --- | --- |
| FEATURE_DISABLED / INVALID_REQUEST / SNAPSHOT_MISMATCH / CONTRACT_MISMATCH | 前置拒绝，不创建线程 |
| TASK_MAPPING_UNSUPPORTED / CAPABILITY_UNAVAILABLE / AUTH_MODE_UNSUPPORTED | 前置失败，不自动更换合同、API或权限 |
| EXECUTION_BUSY / LEASE_FENCED | 不发送新调用；旧输出仅审计 |
| TASK_LIMIT / TURN_LIMIT / ROUND_LIMIT / SOURCE_LIMIT / TIME_LIMIT / TOKEN_SOFT_LIMIT | 本 lane 不再 admission；无其他失败时 exhausted |
| ACCOUNT_RATE_LIMITED | 暂停全执行；显式 continue 重查，不购买或重置额度 |
| USAGE_UNAVAILABLE / SESSION_DRIFT | 暂停 lane；按 System Design 时限结束或等待受信核对 |
| NEEDS_SESSION_RECONCILIATION / TURN_OUTCOME_UNKNOWN / TURN_TERMINATION_UNKNOWN | 维持未知状态与占槽，不盲创建/重发/重试 |
| PROVIDER_TIMEOUT / PROVIDER_TRANSPORT_ERROR | 确认旧 turn 终止后可在配额内重试，否则未知 |
| SCHEMA_VALIDATION_FAILED / EVIDENCE_ID_OUT_OF_SCOPE / SOURCE_DATE_INVALID / SOURCE_UNVERIFIABLE | 按 research/assess 的逐项与 task 规则拒绝，保留兄弟任务 |
| FINALIZATION_FAILED | 结果无效，执行 failed，已验收任务产物仍可读 |
| IDEMPOTENCY_CONFLICT / ARTIFACT_MISSING | 拒绝覆盖；需检查持久记录 |
| MIGRATION_REQUIRED | 非空不兼容数据库零写入拒绝 |
| CLEANUP_SCOPE_UNSAFE / ARCHIVE_FAILED / UI_STORE_MISMATCH | 清理失败或未验证，不删除、不重新研究 |

所有失败返回 code、message、retryable、execution_id、可选 lane/task/attempt ID、safe details。不得返回 credentials、auth file 内容、任意文件路径、SQL 或原始异常秘密。操作员可在本地受保护日志读取协议诊断，不开放公共 endpoint。

## 6. 配置、冻结与公共平台

新 Profile 候选路径在公共平台 `services/mastra-coding-platform/profiles/financial-agent-theme-stage3-codex-sessions-v1.candidate.json`，在人工批准前不注册。冻结后生成独立 active Profile，不修改旧 ablation Profile；Registry 中仅为新条目启用。Profile 引用公共 Prompt 原路径及 SHA，不复制内容。

Coding start 请求在批准后为 `{"projectProfile":"financial-agent-theme-stage3-codex-sessions-v1","task":"Implement the approved Stage 3 six-dimension Codex-session candidate research path with independent usage limits, bounded concurrency, evidence validation, durable recovery and archive-after-persistence; preserve canonical production behavior."}`。只向 LangGraph localhost:4130 提交一次；公共 Mastra service 被 LangGraph bridge 复用，不代表第二个 Mastra Coding run。

冻结批准只允许新文件的 bundle_status 改为 FROZEN_FOR_IMPLEMENTATION、human_approval 改为 APPROVED 并写用户实际批准引用，外加新 Profile/Registry 条目的激活；文档字节保持已审核版本。若内容需改，重新检查并出示新 SHA。不要将候选 Bundle hash、未来冻结后的 Bundle hash、产品 candidate_result_sha256、平台候选源码 snapshot SHA 混为一谈。

此候选只授权文档准备；真实 Coding run 必须等新 Frozen Bundle 和 Profile 生效。Code P0/P1 走平台 Developer loop；Document P0/P1 停止并回到文档审核；Human Final Confirmation 的 exact SHA 和开放 P2 由平台输出，当前 agent 不代替人工 GO。
