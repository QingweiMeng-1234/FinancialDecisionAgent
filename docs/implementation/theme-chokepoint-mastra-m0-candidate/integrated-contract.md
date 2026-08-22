# Theme Chokepoint × Mastra M0 集成实现合同（候选）

```text
document_id: THEME-CHOKEPOINT-MASTRA-M0-INTEGRATED-CONTRACT
document_version: 0.1-candidate.1
document_status: CANDIDATE_AWAITING_HUMAN_APPROVAL
document_roles: [PRD, ADR, SYSTEM_DESIGN, API_CLI_CONTRACT]
language: zh-CN
```

本文件是 Theme Chokepoint 的 Mastra M0 集成合同候选。它只定义集成边界，不替代已有 Theme Chokepoint 产品、证据、评分或 Stage 1–7 合同。人工批准并生成独立的 `bundle.json` 前，不得据此派发 Developer。

## 1. PRD

### 1.1 目标

M0 为既有 Python Theme Chokepoint Stage 1–7 状态机增加一个可恢复的 Mastra 运行时入口：

1. Mastra 创建并持久化一个研究 Workflow run；
2. 通过本机 Streamable HTTP MCP 调用 Python 启动 Stage 1；
3. Python 返回 `AWAITING_PRODUCT_CONFIRMATION` 时，Mastra 使用标准 `suspend()` 暂停；
4. 调用方可读取候选产品锚点；
5. 用户以非空 actor 和候选 anchor ID 恢复同一个 Workflow run；
6. Mastra 先调用 Python 确认锚点，再调用 Python `continue_run`；
7. 状态、Stage receipt 和 artifact ID 可在进程重启后继续读取。

### 1.2 产品边界

M0 包含：

- Python 生命周期 MCP 工具；
- 标准 Mastra Workflow、`suspendSchema`、`resumeSchema`、workflow state 和持久化 storage；
- Mastra run ID 与 Python run ID 的持久化关联；
- 程序化 start/resume/status/artifacts 接口；
- MCP allow-host 约束、严格输入输出校验、失败关闭；
- 单元及本机集成测试，包括真实 RED/GREEN 记录。

M0 不包含：

- Web 产品界面或人工确认页面；
- Stage 6 新的实时事件源、定时器或监控界面；
- QuantGPT 接入；
- 新的 ThemeFramer、ProductAnchorProposer 或其他 live provider；
- 复制或重写 Python Stage 1–7 状态逻辑；
- 修改评分、证据、数据治理、Stage 2–7 语义；
- 对既有 SQLite schema 或既有持久化数据做 migration。

M0 的“可运行”证明限于：使用依赖注入的现有 Python runtime 和本机 MCP transport，完成 Stage 1 暂停、人工确认、继续与重启恢复。没有配置 live provider 时，系统必须明确启动失败，不得伪造生产研究结果。

### 1.3 用户故事

- 作为研究员，我可以提交一个 assisted Theme Chokepoint 请求，并看到 Python 生成的候选产品锚点。
- 作为研究员，我可以选择一个或多个候选锚点并署名确认，然后让同一 Python run 继续。
- 作为运维人员，我可以在 Mastra 进程重启后用原 Mastra run ID 查询或恢复任务。
- 作为审计者，我可以确认每次状态变化来自 Python 权威状态机，Mastra 没有自行推导 Stage 2–7 状态。

### 1.4 Acceptance Criteria

#### AC-M0-01：Python 生命周期工具完整

Python MCP surface 必须提供以下六个工具，并使用本合同第 4 节的 schema：

- `theme_chokepoint_start`
- `theme_chokepoint_get_run`
- `theme_chokepoint_get_pending_anchors`
- `theme_chokepoint_confirm_anchors`
- `theme_chokepoint_continue`
- `theme_chokepoint_get_artifacts`

既有 `theme_chokepoint_finalize`、`theme_chokepoint_compare_runs`、`theme_chokepoint_record_feedback` 保持兼容。

#### AC-M0-02：只在权威确认状态暂停

Mastra 启动 Stage 1 后必须重新读取 Python 状态。仅当精确状态为 `AWAITING_PRODUCT_CONFIRMATION` 时调用标准 Mastra `suspend()`。`NEEDS_CLARIFICATION`、unknown、tool error 或 schema mismatch 均不得伪装成待确认状态。

#### AC-M0-03：候选锚点来自 Python

暂停数据中的 anchor 必须来自同一 Python run 的 `theme_chokepoint_get_pending_anchors` 返回值。Mastra 不得创建、修改、补全或重新排序 anchor ID。

#### AC-M0-04：恢复输入严格验证

resume 必须包含：

- 原 Mastra run ID；
- 非空、trim 后仍非空的 `actor`；
- 1 个或多个互不重复的 `selected_anchor_ids`。

所有 selected ID 必须属于当前同一 Python run 的待确认候选集合。重复 ID、空 actor、foreign ID、unknown ID、错误类型或额外未声明字段均失败关闭。

#### AC-M0-05：确认门不可绕过

Mastra 只有在 Python `theme_chokepoint_confirm_anchors` 成功返回同一 run ID 和 `READY_FOR_SUPPLY_CHAIN` 后，才可调用 `theme_chokepoint_continue`。确认失败、响应 run ID 不匹配、状态不匹配或 MCP 异常时，continue 调用次数必须为零。

#### AC-M0-06：Python 保持唯一状态 authority

Mastra 不实现 Stage 1–7 转移表，不直接写 Theme SQLite，不调用 Stage 2–7 service，不把本地推断状态写回 Python。Mastra 只保存关联信息、最后观察到的 Python 状态和 suspend/resume 所需数据。

#### AC-M0-07：持久化与重启恢复

Mastra 必须配置持久化 storage。关闭并重新构造 Mastra runtime 后，原 Mastra run ID 必须仍能：

- 找到 workflow snapshot；
- 找到关联的 Python run ID；
- 读取当前状态；
- 在仍待确认时恢复原 gate。

不得以仅内存 mock 作为本条通过证据。

#### AC-M0-08：并发和重复恢复失败关闭

对同一 suspended run 的两个并发或重复 resume，最多一次可完成 Python 确认。失败调用不得第二次推进 Python，也不得覆盖首次确认人或确认锚点。最终状态以 Python 为准。

#### AC-M0-09：状态与 artifacts 可读取

`getM0Status` 必须返回 Mastra run ID、Python run ID、Mastra workflow status、最后实时读取的 Python status 以及是否可恢复。`getM0Artifacts` 必须返回 Python root manifest 的 stage receipts 和 artifact IDs；不得扫描或返回合同之外的任意文件。

#### AC-M0-10：安全与错误语义

- MCP URL 只允许配置为 `http://127.0.0.1:<port>/mcp` 或 `http://localhost:<port>/mcp`；
- Mastra `MCPClient` 必须配置对应 `allowedHosts`；
- 错误输出不得包含环境变量、认证 token、完整 provider 原始响应或本机无关路径；
- malformed JSON、非有限数值、unknown 状态、响应 schema mismatch、storage error 和 MCP error 均失败关闭；
- 任何失败都不得被转换为成功、暂停或已确认。

#### AC-M0-11：严格 TDD 与回归

每个新增运行时 invariant 必须先有 focused test 并观察到预期 RED，再实现 GREEN。至少运行：

- Python lifecycle/interface focused tests；
- Python MCP registration focused tests；
- Mastra workflow unit tests；
- 使用临时 LibSQL 文件和 fake/local MCP server 的 restart integration test；
- 既有 `test_theme_chokepoint_stage1.py`、`test_theme_chokepoint_orchestrator.py`、`test_financial_agent_mcp.py` 相关回归；
- TypeScript typecheck 和 test suite。

报告必须给出真实命令、退出码、RED 原因、GREEN 结果和证明限制。

#### AC-M0-12：范围和兼容性

- 既有 Python Stage 1–7、scoring v1.4/v1.5、repository schema 和 artifact 语义保持不变；
- 不得删除、skip、xfail 或弱化既有测试；
- 不得把当前仓库其他未提交改动纳入 candidate；
- Mastra 包必须位于独立目录，不能改造成新的 Python authority。

## 2. ADR

### ADR-M0-01：采用“双控制面、单状态 authority”

状态：候选，随本文件一起批准。

决策：

- Multica + 已发布 TC Core Prompt 只承担开发任务编排、Developer 写权限、TDD、独立评审和人工 GO 流程。
- Mastra 承担产品运行时 Workflow、suspend/resume、MCP 连接、持久化 snapshot 和可观察状态。
- Python `RootStageOrchestrator`、`AssistedThemeFramingService`、`ThemeChokepointRepository` 继续承担业务状态和 Stage 1–7 转移 authority。

理由：既有 Python 已实现 durable confirmation gate、Stage receipts、SQLite 事务和评分合同。复制状态机将产生双写、状态漂移和绕过人工确认的风险。

后果：Mastra 的状态只能表示运行时协调信息；发生差异时 Python 状态优先，Mastra 返回 conflict/error，而不是自行修复 Python。

### ADR-M0-02：使用标准 Mastra Workflow 原语

决策：使用 `createWorkflow`、`createStep`、`suspendSchema`、`resumeSchema`、`workflow.createRun({ runId })`、`run.start()`、`run.resume()` 和持久化 Mastra storage。不得自建等价的 TypeScript 状态机或自制 suspend 表。

工作流固定为三个业务步骤：

1. `start-python-stage1`
2. `await-product-confirmation`
3. `continue-python-run`

只允许第二步 suspend。

### ADR-M0-03：通过本机 Streamable HTTP MCP 隔离语言边界

决策：Mastra 使用 `@mastra/mcp` 的 `MCPClient` 连接现有 Python FastMCP server。连接首选 Streamable HTTP，允许库自身的 SSE fallback；不通过 shell、Python child process 或直接 SQLite 访问实现业务调用。

理由：MCP 提供稳定工具 schema、独立进程边界和未来 UI/agent 的复用面。

### ADR-M0-04：M0 不增加既有数据 migration

决策：Python 既有 SQLite schema 不变。Mastra storage 使用独立新文件，只保存 Mastra 自身 workflow snapshot 和 state。这是新组件初始化，不是既有持久化数据/schema migration，因此本 bundle 不要求 Migration Contract。

### ADR-M0-05：live provider composition 明确依赖注入

决策：M0 不臆造缺失的默认 `ThemeFramer` 或 `ProductAnchorProposer`。Python MCP server 接受已构造的 `ThemeChokepointInterface`/runtime 注入；未注入或配置不完整时，Theme lifecycle tools 不得假装可用，并返回明确的 configuration error。

## 3. System Design

### 3.1 组件

```text
Caller / later UI
        |
        v
Mastra M0 service
  - standard Workflow
  - persistent storage
  - run correlation state
        |
        | Streamable HTTP MCP (localhost only)
        v
FinancialAgentMCPServer
        |
        v
ThemeChokepointInterface
  - input/output schema boundary
        |
        +--> AssistedThemeFramingService (confirm)
        +--> RootStageOrchestrator (start/continue/manifest)
        +--> ThemeChokepointRepository (read status/anchors)
```

### 3.2 Python composition

`ThemeChokepointInterface` 扩展为 lifecycle facade，并显式接收：

- `repository`
- `product_service`（保留既有 finalize/compare/feedback）
- `stage1`
- `orchestrator`

为保护既有调用者，旧的只读/产品方法保持签名和返回兼容；lifecycle handler 只有在 `stage1` 与 `orchestrator` 均存在时才注册。`FinancialAgentMCPServer` 接受可选的已构造 interface，并只注册 security allow-list 中允许的工具。

M0 不在 `FinancialAgentMCPServer` 内构造 live Theme providers。

### 3.3 Mastra package

新增独立包：`apps/theme-chokepoint-mastra/`。它至少包含：

- package manifest 和锁定依赖；
- Mastra instance 与持久化 storage 配置；
- MCP client factory；
- workflow 与 Zod schemas；
- programmatic service facade；
- unit/integration tests；
- 仅说明运行方式和 proof limits 的 README。

实现使用与开发当日 stable Mastra 1.x API 兼容的固定精确版本，并将锁文件纳入 candidate。不得使用浮动 `latest` 或无上限版本范围。

### 3.4 Workflow state

持久化 state 只包含：

```json
{
  "schemaVersion": "theme-chokepoint-m0-state.v1",
  "mastraRunId": "uuid",
  "pythonRunId": "uuid",
  "lastPythonStatus": "AWAITING_PRODUCT_CONFIRMATION",
  "pendingAnchorIds": ["anchor-id"],
  "confirmation": null
}
```

确认后 `confirmation` 只保存 actor、selected IDs 和 Python receipt 时间。state 不保存 token、provider raw body 或完整研究 artifacts。

### 3.5 启动时序

1. facade 生成一个 UUID，并同时作为 Mastra run ID 和请求的 Python `run_id`；两个字段仍分别持久化，禁止依赖“永远相同”的隐含假设。
2. 创建 run，使用 `initialState` 写入关联。
3. `start-python-stage1` 调用 Python start。
4. 校验响应 run ID 与 state 中 Python run ID 相同。
5. `await-product-confirmation` 实时调用 get-run。
6. 若 awaiting，调用 get-pending-anchors，校验并写入 state，然后 suspend。
7. 若 needs clarification，正常返回受控结果，不 suspend、不 continue。
8. 其他已知 Python 状态按第 3.8 节处理；unknown 状态失败关闭。

### 3.6 恢复时序

1. facade 通过持久化 storage 取得原 workflow run；不存在则返回 not-found。
2. 调用方对 `await-product-confirmation` step 执行 resume。
3. step 重新读取 Python 状态和 pending anchors，不信任旧 suspend payload。
4. 校验 actor 和 selected IDs。
5. 调用 confirm；严格校验 receipt。
6. 只有确认成功后进入 `continue-python-run`。
7. continue 的最终输出原样携带 Python status 与 manifest 摘要。

### 3.7 并发策略

Mastra 层不提供“最后写入获胜”。并发 resume 都必须重新读取 Python；Python SQLite confirmation transaction 是最终串行化点。一个请求确认后，其他请求观察到非 awaiting 或收到 Python rejection，必须失败且不得 continue。

### 3.8 状态映射

Mastra 只使用以下协调规则，不复制 Stage 转移：

- `AWAITING_PRODUCT_CONFIRMATION`：suspend；
- `NEEDS_CLARIFICATION`：完成当前调用并返回 `needs_clarification`；
- `READY_FOR_SUPPLY_CHAIN`：只允许在已有有效 confirmation receipt 的恢复路径进入 continue；
- 其他已知 Python 状态：作为 Python current status 返回，不由 Mastra跳转 Stage；
- unknown/string mismatch：error。

### 3.9 恢复与冲突

Mastra snapshot 缺失但 Python run 存在时，M0 不自动重建 workflow history；返回 `MASTRA_RUN_NOT_FOUND` 并保留 Python run。Mastra snapshot 存在但 Python run 缺失时返回 `PYTHON_RUN_NOT_FOUND`。run ID mismatch 返回 `RUN_CORRELATION_MISMATCH`。这些状态均不得自动创建替代 run。

### 3.10 可观察性

日志允许记录：Mastra run ID、Python run ID、tool name、开始/结束时间、结果 status、错误 code。日志不得记录完整 input prompt、environment、认证信息、provider raw response 或超出本合同的文件内容。

## 4. API / CLI Contract

### 4.1 通用规则

- 所有 JSON object 使用严格 schema，未知字段拒绝；
- 日期使用 `YYYY-MM-DD`，时间使用带时区 ISO-8601；
- ID 为 trim 后 1–128 字符的非空字符串；
- number 必须 finite；budget 必须满足现有 Python `ResearchRequest` 规则；
- 成功返回 `{ "ok": true, "data": ... }`；
- 可预期错误返回 `{ "ok": false, "error": { "code", "message", "retryable" } }`；
- message 只含安全摘要；未知异常由 transport 标记 tool failure，不能返回伪成功。

### 4.2 `theme_chokepoint_start`

输入：

```json
{
  "run_id": "uuid",
  "theme": "string",
  "trigger": "string",
  "region": "string",
  "as_of_date": "2026-08-22",
  "time_horizon_months": 24,
  "analysis_goal": "string",
  "seed_products": [],
  "seed_companies": [],
  "research_mode": "assisted",
  "max_depth": 4,
  "max_nodes": 50,
  "max_iterations": 3,
  "max_sources": 30,
  "max_time_seconds": 900,
  "max_cost_usd": 10.0,
  "max_product_anchors": 5
}
```

成功 data：

```json
{
  "schema_version": "theme-chokepoint-mcp.v1",
  "run_id": "uuid",
  "status": "AWAITING_PRODUCT_CONFIRMATION",
  "next_stage": null
}
```

它必须调用 `RootStageOrchestrator.start(ResearchRequest)`，不得仅调用 repository。

### 4.3 `theme_chokepoint_get_run`

输入：`{ "run_id": "uuid" }`

成功 data 至少包含：`schema_version`、`run_id`、`status`、`next_stage`、`confirmed_by`、`confirmed_at`。`confirmed_by` 与 `confirmed_at` 未确认时为 null。

### 4.4 `theme_chokepoint_get_pending_anchors`

输入：`{ "run_id": "uuid" }`

仅当 Python status 为 `AWAITING_PRODUCT_CONFIRMATION` 时成功。data：

```json
{
  "schema_version": "theme-chokepoint-mcp.v1",
  "run_id": "uuid",
  "status": "AWAITING_PRODUCT_CONFIRMATION",
  "demand_frame": {},
  "anchors": [
    {
      "anchor_id": "string",
      "product_name": "string",
      "buyer_or_user": "string",
      "demand_variable": "string",
      "theme_link": "string",
      "confidence": 0.8,
      "supporting_evidence_ids": [],
      "missing_evidence": [],
      "status": "proposed"
    }
  ]
}
```

### 4.5 `theme_chokepoint_confirm_anchors`

输入：

```json
{
  "run_id": "uuid",
  "anchor_ids": ["anchor-id"],
  "confirmed_by": "human-actor"
}
```

成功 data：`schema_version`、`run_id`、`status = READY_FOR_SUPPLY_CHAIN`、`confirmed_anchor_ids`、`confirmed_by`、`confirmed_at`。

interface 在调用 stage1 前拒绝空数组、重复 ID、空 actor 和非字符串。Python repository 继续负责 foreign/unknown ID、非 awaiting 状态与 transaction authority。

### 4.6 `theme_chokepoint_continue`

输入：`{ "run_id": "uuid" }`

它必须调用 `RootStageOrchestrator.continue_run(run_id)`。成功 data 至少包含：`schema_version`、`run_id`、`status`、`stages[]`。M0 不解释或改写 Stage 2–7 receipt。

### 4.7 `theme_chokepoint_get_artifacts`

输入：`{ "run_id": "uuid" }`

它通过 orchestrator 的公开只读 manifest 方法返回：`schema_version`、`run_id`、`status`、`stages[]`，每个 stage 含 `stage`、`input_status`、`output_status`、`outcome`、`artifact_ids`、`completed_at`。不得接受任意 path 参数。

### 4.8 错误 code

稳定错误 code：

- `INVALID_ARGUMENT`
- `RUN_ALREADY_EXISTS`
- `RUN_NOT_FOUND`
- `RUN_NOT_AWAITING_CONFIRMATION`
- `ANCHOR_NOT_PROPOSED_FOR_RUN`
- `DUPLICATE_ANCHOR_ID`
- `RUNTIME_NOT_CONFIGURED`
- `RUN_CORRELATION_MISMATCH`
- `PYTHON_STATUS_MISMATCH`
- `MCP_RESPONSE_SCHEMA_MISMATCH`
- `MCP_TOOL_FAILURE`
- `MASTRA_RUN_NOT_FOUND`
- `STORAGE_FAILURE`
- `CONCURRENT_OR_DUPLICATE_RESUME`
- `UNKNOWN_PYTHON_STATUS`

### 4.9 Mastra programmatic facade

导出以下函数或等价 class methods：

```ts
startM0(input: ResearchRequestInput): Promise<StartResult>
resumeM0(input: { mastraRunId: string; actor: string; selectedAnchorIds: string[] }): Promise<ResumeResult>
getM0Status(mastraRunId: string): Promise<StatusResult>
getM0Artifacts(mastraRunId: string): Promise<ArtifactsResult>
```

Workflow ID 固定为 `theme-chokepoint-m0`，可 suspend 的 step ID 固定为 `await-product-confirmation`。对外字段使用 camelCase；MCP adapter 在边界处显式转换 snake_case，禁止散落的隐式字段转换。

### 4.10 配置

允许的配置项：

- `THEME_CHOKEPOINT_MCP_URL`，默认 `http://127.0.0.1:8877/mcp`；
- `MASTRA_STORAGE_URL`，默认 package 工作目录下的独立 LibSQL 文件 URL；
- `LOG_LEVEL`。

MCP URL host 不在 `127.0.0.1` 或 `localhost` 时启动失败。M0 不新增 provider credential 配置。

## 5. 实现范围

### 5.1 Allowed scope

- `apps/theme-chokepoint-mastra/**`
- `src/event_collector/theme_chokepoint/interfaces.py`
- `src/event_collector/theme_chokepoint/orchestrator.py`
- `src/event_collector/financial_agent_mcp.py`
- `src/event_collector/__init__.py`
- `tests/test_theme_chokepoint_interfaces.py`
- `tests/test_theme_chokepoint_orchestrator.py`
- `tests/test_financial_agent_mcp.py`

### 5.2 Forbidden scope

- 本候选合同及其后发布的 Frozen Bundle
- `src/event_collector/theme_chokepoint/contracts.py`
- `src/event_collector/theme_chokepoint/repository.py`
- `src/event_collector/theme_chokepoint/stage1.py`
- `src/event_collector/theme_chokepoint/stage2.py`
- `src/event_collector/theme_chokepoint/stage3.py`
- `src/event_collector/theme_chokepoint/stage4.py`
- `src/event_collector/theme_chokepoint/stage5.py`
- `src/event_collector/theme_chokepoint/stage6.py`
- `src/event_collector/theme_chokepoint/stage7.py`
- `src/event_collector/theme_chokepoint/providers/**`
- `src/event_collector/theme_chokepoint/production.py`
- `docs/product/theme-chokepoint-scoring-*`
- `docs/adr/0002-theme-chokepoint-evidence-trust-architecture.zh-CN.md`
- `docs/adr/0003-theme-chokepoint-authoritative-verification-and-assessment.zh-CN.md`
- 任何既有 SQLite/Chroma/database 文件或 migration
- `QuantGPT/**`
- 仓库中与本 M0 无关的 staged、modified 或 untracked 文件

## 6. Acceptance checks

Developer 交付前至少执行并记录：

1. Python lifecycle/interface focused tests；
2. Python MCP registration focused tests；
3. Mastra workflow focused tests；
4. Mastra restart/reopen integration test（临时持久化 LibSQL 文件）；
5. concurrent/duplicate resume focused test；
6. malformed input、strict JSON、non-finite number、error redaction、unknown state 边界矩阵；
7. `tests/test_theme_chokepoint_stage1.py`；
8. `tests/test_theme_chokepoint_orchestrator.py`；
9. `tests/test_financial_agent_mcp.py`；
10. Mastra package typecheck；
11. Mastra package full test suite；
12. candidate 相对 exact baseline 的 scope diff。

## 7. 人工批准语义

对本文件的批准必须引用精确 SHA-256。批准后由 Document Owner 复制 exact bytes 到 append-only published bundle 目录，生成 `bundle.json`，设置：

- `bundle_status = FROZEN_FOR_IMPLEMENTATION`
- `changes_existing_persisted_data = false`
- `human_approval.status = APPROVED`
- `human_approval.reference` 为批准该 SHA-256 的人工消息/turn 引用

“批准本方向”“可以开始”或未绑定 SHA 的早期对话，不等于批准本候选 exact bytes。
