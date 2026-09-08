# Theme Chokepoint 单 Agent / 多 Agent 可控执行实验 PRD

## 文档控制

| 字段 | 值 |
| --- | --- |
| 状态 | `FROZEN_FOR_IMPLEMENTATION` |
| 产品版本 | `1.0` |
| 日期 | `2026-09-04` |
| 冻结日期 | `2026-09-04` |
| 产品 | Financial Agent / Theme Chokepoint Research |
| 文档类型 | 执行架构对照实验 PRD |
| 父级产品 PRD | `docs/product/theme-chokepoint-research-agent-prd.md` |
| System Design | `docs/architecture/theme-chokepoint-agent-execution-ablation-system-design.md` |
| API/Data Contract | `docs/api/theme-chokepoint-agent-execution-ablation-api.md` |
| Freeze manifest | `docs/product/theme-chokepoint-agent-execution-ablation-freeze-manifest-v1.0.json` |
| 当前评分语义 | 继续使用运行所声明的版本化 Theme Chokepoint 合同；本 PRD 不修改评分含义 |
| 首期实现范围 | Stage 3 证据任务执行与盲审反证；Stage 4 独立验证为第二工作包 |
| 框架决策 | 延后；本 PRD 不选择 Mastra、LangGraph 或其他 Agent 框架 |

## 1. 产品决策

Theme Chokepoint 应当把“研究协议”和“Agent 执行拓扑”分开。

研究协议继续由确定性 Orchestrator、持久化状态、证据合同、评分合同和验证门控制；Agent 只是执行需要语义判断的工作单元。系统必须同时支持以下两种可比较的执行模式：

```text
single
  一个共享推理上下文按顺序完成证据研究和反证研究

isolated_workers
  一个确定性 Orchestrator 将可分片任务交给隔离上下文中的专职 worker，
  并将反证研究交给看不到主结论的独立 critic worker
```

两种模式必须使用相同的研究范围、来源快照、总预算、事实 schema、评分合同、验证器和最终输出合同。多 Agent 模式不得通过获得更多预算、更宽来源、不同评分规则或额外私有信息来制造表面优势。

本实验不把“多 Agent 更聪明”设为产品假设。它要验证的是多 Agent 是否在以下四个系统属性上产生可测量收益：

1. 并行执行；
2. 上下文隔离；
3. 独立复核；
4. 故障隔离与定向恢复。

研究准确率、召回率或最终结论质量必须独立测量，不能从 Agent 数量推导。

## 2. 背景与当前实现

当前 `RootStageOrchestrator` 按持久化 `RunStatus` 顺序推进 Stage 1–7，并为完成的阶段记录 receipt。Stage 3 的 `EvidenceChokepointLoop` 根据缺失的 `node × material_field` 生成查询、获取证据、物化 Claim/EvidenceCard/SourceSnapshot、重新评分并迭代到完整或预算耗尽。

当前实现具有以下特征：

- Stage 1–7 是顺序状态机，而不是并行 Agent graph；
- Stage 3 的 `node × material_field` 获取循环顺序执行；
- Segment critic 在初步 ordinal draft 之后执行，并接收该 draft，因此当前 critic 不是盲审；
- v1.6.1 fact adapter 已禁止模型直接提交 ordinal rating/state；
- schema 校验、证据 ID case binding 和确定性评分由代码执行；
- counter-search 已有 demand、supply、alternatives 三条固定路线，并保存 request、raw response、parsed result 与 reconciliation 血缘；
- Production composition 已将 company discovery、evidence、scoring、critic、fact verification 和 source resolution 配置为隔离 provider 边界。

因此，本项目不需要重写 Theme Chokepoint。首期只需在既有 Stage 3 seam 中加入可替换执行策略，并把 critic 输入改造成可验证的盲审任务。

## 3. 问题陈述

当前架构无法回答以下问题：

1. 多 Agent 相比单 Agent，到底提高了研究质量，还是仅缩短了可并行任务的执行时间？
2. critic 是否真正独立，还是在看到主研究结论后对原结论进行自洽式复述？
3. 一个节点或字段失败时，系统能否保存其他已成功任务并只重试失败分片？
4. 多 Agent 是否通过获得更多 token、查询、来源或成本预算获得不公平优势？
5. 如果换用 Mastra 或 LangGraph，框架是在解决真实执行需求，还是仅仅把顺序函数包装成图？

没有可切换执行模式、任务级 receipt 和配对实验，上述问题只能靠主观演示回答。

## 4. 产品目标

### 4.1 目标

1. 在不改变 Stage 3 最终领域合同的前提下支持 `single` 与 `isolated_workers` 两种模式。
2. 将每个证据缺口表示为稳定、可持久化、可重试的任务。
3. 保证两种模式共享相同输入、预算、验证和评分规则。
4. 让 multi 模式可以对独立任务进行有界并行。
5. 让 counter-evidence TaskPacket 不含主研究的分数、状态和总结，并单独测量 shared session 与 isolated fresh context 的盲审差异。
6. 一个 worker 失败时保存其余成功结果，并只重试失败任务。
7. 记录足以比较延迟、上下文、成本、失败恢复和质量的运行数据。
8. 在选择 Mastra/LangGraph 前冻结框架无关的任务与执行合同。

### 4.2 非目标

本 PRD 不负责：

- 证明多 Agent 在所有主题和模型上拥有更高研究准确率；
- 修改 Theme Chokepoint 评分维度、权重、hard gate 或最终状态语义；
- 重写 Stage 1、Stage 2 或 Stage 5–7；
- 将所有函数或 provider 命名为 Agent；
- 引入 Agent 投票或用多数票替代证据合同；
- 扩大 live provider 预算或授权新的付费来源；
- 自动交易、投资建议或组合构建；
- 在本 PRD 中选定 Mastra、LangGraph 或部署拓扑。

## 5. 目标用户与 Jobs to Be Done

### 5.1 研究用户

研究用户需要在相同主题和证据条件下获得可信、可追溯的研究产物，不因系统切换为多 Agent 而改变评分含义或隐藏不完整状态。

### 5.2 开发者

开发者需要通过一个配置项切换执行模式，复用相同 fixtures、contracts 和 assertions，并定位任务级失败。

### 5.3 评估者

评估者需要获得 paired-execution 报告，回答：

- 两种模式是否处理了同一组任务？
- 是否使用同一输入快照和总预算？
- 并行是否真实发生？
- critic 是否满足盲审约束？
- 多 Agent 是否改善、维持或损害了质量指标？
- 差异来自模型输出，还是来自执行、重试、预算或输入不一致？

## 6. 核心术语

| 术语 | 定义 |
| --- | --- |
| Research protocol | Stage 顺序、输入输出合同、证据规则、评分规则、预算和状态门；与 Agent 数量无关。 |
| Execution mode | 同一 research protocol 的执行拓扑，首期为 `single` 或 `isolated_workers`。 |
| Single Agent | 一个共享推理上下文按顺序处理当前迭代中的研究与反证任务。 |
| Isolated worker | 只接收一个最小 TaskPacket、没有其他任务对话历史的推理执行体。 |
| Orchestrator | 不负责自由研究的确定性控制器；负责计划、分发、预算、状态、合并和持久化。 |
| Base run | 已完成 Stage 2、其 Stage 3 输入已冻结的领域运行；`base_run_id` 是配对实验的共同起点，不代表任一实验分支已经写入正式 Stage 3。 |
| Execution | 在一个冻结 Stage 3 输入上按某种 execution mode 执行一次完整 Stage 3；由 `execution_id` 标识并拥有独立候选结果命名空间。 |
| Evidence task | 一个确定的 `frozen Stage-3 snapshot × iteration × node × material_field` 证据工作单元。 |
| Blind counter task | 不包含主研究分数、候选状态或结论摘要的反证任务。 |
| Hook / gate | 在任务或 provider I/O 前后自动执行的确定性校验、持久化、限流、重试或阻断逻辑。 |
| Task receipt | 描述任务输入身份、执行身份、状态、成本、产物和错误的不可变记录。 |
| Paired execution | 对同一个 `base_run_id` 和冻结 Stage 2 快照分别执行 single 与 isolated-workers，并通过 `pair_id` 关联的两个隔离 execution。 |
| Strict-replay plan | 在任一 arm 执行前封存、两边完整重放的有序任务计划；用于可归因于 execution topology 的 Phase A/B 对照。 |

## 7. Stage 责任决策

| Stage | v1.0 执行决策 | 理由 |
| --- | --- | --- |
| Stage 1：Theme framing / product anchors | 一个逻辑 Agent | 需要统一理解用户目标；接口分离不要求身份或上下文分离。 |
| Stage 2：Supply-chain graph | 默认一个逻辑 Agent | 中小图需要全局一致性；并行扩展留作后续实验。 |
| Stage 3：Evidence and chokepoint loop | 单 Agent 基线 + isolated worker 实验 | `node × material_field` 可自然分片；counter-search 可独立盲审。 |
| Stage 4：Company exposure / red team | 第二工作包加入单 Agent 与独立 verifier/critic 对照 | 公司事实验证具有高风险推理边界，但不应扩大首期 Stage 3 改造。 |
| Stage 5：Persistent product | 确定性代码 | 文件、manifest、hash、resume 和 compare 不需要 Agent。 |
| Stage 6：Monitoring | 一个语义 evaluator + 确定性调度 | 首期不做多 Agent；以后可按受影响对象 fan-out。 |
| Stage 7：Signal export | 确定性代码 | 同一 research state 必须产生稳定、可验证的导出。 |

## 8. 实验组定义

### 8.1 A 组：Single Agent

```text
RootStageOrchestrator
  → Stage3 planner 生成当前迭代全部 EvidenceTask
  → 一个共享 Agent session 顺序执行 task 1..N
  → 同一个 session 顺序执行 counter tasks
  → validator/materializer
  → deterministic scorer
```

要求：

- task 顺序必须稳定；
- 每个任务仍产生独立 receipt；
- single 模式不得跳过反证、验证或持久化；
- single 模式不得获得比 multi 更少的总研究预算；
- 共享上下文大小与压缩事件必须记录；
- 反证任务使用与 multi 相同的禁止字段 TaskPacket，但因为 session 保留主研究历史，single 只能标记 `packet_blind=true`、`context_blind=false`，不得声称独立盲审。

### 8.2 B 组：Isolated Workers

```text
RootStageOrchestrator
  → Stage3 planner 生成与 A 组相同的 EvidenceTask
  → bounded fan-out：Evidence Worker 1..N
  → validator/materializer
  → Blind Counter Worker 1..M
  → validator/reconciliation
  → deterministic scorer
```

要求：

- 每个 worker 使用全新或可证明隔离的上下文；
- worker 只能收到显式 TaskPacket；
- 并发数必须有上限；
- 输出合并顺序必须稳定，不依赖 completion order；
- critic worker 不得收到禁止字段；
- critic 必须使用与 primary evidence 不同的 `worker_id` 和不含 primary transcript 的 fresh context，并记录 `packet_blind=true`、`context_blind=true`；
- 一个 worker 的失败不得删除其他 worker 已提交的有效产物。

### 8.3 公平性约束

配对 execution 必须绑定同一个 `base_run_id` 和以下相同身份：

```text
theme scope hash
as_of_date
region and horizon
supply-chain graph snapshot hash
source corpus/generation snapshot
model family and version
prompt family/version
fact schema/version
scoring contract ID and SHA-256
governance bundle ID and SHA-256
maximum sources
maximum queries
maximum total input/output tokens where enforceable
maximum time and cost budgets
```

`pair_id` 关联 `base_run_id`、`single_execution_id`、`isolated_execution_id` 和上述公平性身份。任何一项不一致，比较状态必须为 `PAIR_INVALID`，不得生成胜负结论。两个 execution 均写入各自的候选命名空间，不得在对照或 shadow 执行期间直接推进 `base_run_id` 的正式 `RunStatus`。

Phase A/B 的 proof-eligible pair 必须使用 `strict_replay`：在任一 arm 开始前封存完整 `task_key` 顺序，两边只执行该计划。production-like dynamic shadow 可以按各自上轮候选结果规划后续缺口，但如果计划发生分叉，报告必须以 `PAIR_INVALID`（reason=`TASK_PLAN_DIVERGED`）拒绝 topology-only 因果结论；分叉本身只作为观测指标报告。

## 9. 产品架构

```text
                    ┌──────────────────────────┐
                    │ RootStageOrchestrator    │
                    │ status / budget / receipt│
                    └────────────┬─────────────┘
                                 │
                    ┌────────────▼─────────────┐
                    │ Stage3TaskPlanner        │
                    │ missing pairs → tasks    │
                    └────────────┬─────────────┘
                                 │
                     execution_mode switch
                          ┌──────┴──────┐
                          │             │
               ┌──────────▼───┐   ┌────▼─────────────┐
               │ Sequential   │   │ Isolated Workers │
               │ Executor     │   │ bounded fan-out  │
               └──────────┬───┘   └────┬─────────────┘
                          └──────┬──────┘
                                 │
                    ┌────────────▼─────────────┐
                    │ Evidence validator       │
                    │ materializer / ledger    │
                    └────────────┬─────────────┘
                                 │
                    ┌────────────▼─────────────┐
                    │ Blind counter protocol   │
                    └────────────┬─────────────┘
                                 │
                    ┌────────────▼─────────────┐
                    │ Deterministic scorer     │
                    │ Stage3Result             │
                    └──────────────────────────┘
```

## 10. 领域与执行合同

### 10.1 EvidenceTask

首期任务粒度固定为一个 `node × material_field × iteration`：

```json
{
  "task_key": "mode-independent-semantic-hash",
  "task_id": "execution-scoped-id",
  "pair_id": "paired-experiment-id",
  "base_run_id": "frozen-stage2-run-id",
  "execution_id": "single-or-isolated-execution-id",
  "iteration": 1,
  "node_id": "segment-id",
  "node_name": "qualified glass substrate",
  "material_field": "qualification_barrier",
  "scope": {
    "region": "global",
    "as_of_date": "2026-09-04",
    "horizon": "..."
  },
  "query": "deterministically planned or sealed query",
  "allowed_source_policy_id": "...",
  "input_snapshot_sha256": "...",
  "task_budget": {
    "max_queries": 1,
    "max_sources": 5,
    "max_time_seconds": 60,
    "max_cost_usd": 0.50
  }
}
```

身份规则是冻结合同的一部分：

```text
task_key = SHA-256(canonical semantic task payload)
task_id  = SHA-256(execution_id + task_key)
```

`task_key` 的 canonical payload 必须包含冻结 Stage 3 输入 hash、iteration、node、material field、sealed query、source policy、任务预算及相关 contract version；明确不得包含 `base_run_id`、`pair_id`、`execution_id`、`execution_mode`、worker 或 attempt 身份。因而，同一 pair 两边必须拥有完全相同且顺序一致的 `task_key` 集合。

写入 `task_key` 的任务预算只能是 canonicalized 配置上限（例如 max queries/sources/time/cost），不得使用运行时 reservation、实际 token/cost 或完成时间；否则调度差异会污染语义身份。

`task_id` 是 execution-specific 身份；single 与 isolated execution 对应同一 `task_key` 时拥有不同 `task_id`。重复提交同一个 `execution_id + task_key` 不得产生第二个逻辑任务。`attempt_id` 标识该 task 的一次领取/重试，不能充当 task 或 execution 身份。

### 10.2 EvidenceTaskOutcome

```json
{
  "execution_id": "...",
  "task_key": "...",
  "task_id": "...",
  "status": "succeeded | failed_retryable | failed_terminal | exhausted",
  "candidate_ids": [],
  "provider_request_receipt_ids": [],
  "input_tokens": 0,
  "output_tokens": 0,
  "cost_usd": 0,
  "elapsed_ms": 0,
  "error_code": null,
  "error_message": null
}
```

模型生成的自然语言错误不得替代稳定的 `error_code`。

### 10.3 BlindCounterTask

BlindCounterTask 只允许包含：

- `base_run_id`、`execution_id`、`task_key`、`task_id` 及可选 `pair_id`；
- node、region、as-of、horizon；
- 要验证的客观问题；
- 已允许的 source policy；
- demand、supply、alternatives 三条路线；
- 可用于避免重复搜索的 source identity；
- 任务预算。

以下字段明确禁止出现：

```text
provisional_score
score_min / score_max
candidate_chokepoint
competition_state
main_research_summary
recommended_conclusion
researcher_confidence
```

critic 的结果只能增加反证、显式负向搜索结果或 `unknown` 覆盖状态，不能直接写最终 ordinal score。

### 10.4 AgentExecutionReceipt

每次尝试必须记录：

```text
receipt_id
base_run_id
execution_id
task_key
task_id
attempt_id
pair_id (required for paired execution; nullable only for standalone fixture execution)
execution_mode
worker_role
worker_id
packet_blind / context_blind
model_id
prompt_version
input_snapshot_sha256
context_payload_chars/tokens
started_at / finished_at
provider receipt IDs
output artifact IDs
status / error_code
cost and token usage
```

`worker_id` 标识实际执行该 attempt 的 session/worker：single 模式可在多项任务上复用同一个 `worker_id`，isolated 模式必须使用可证明隔离的 worker identity。它不同于整个 arm 的 `execution_id`，也不同于一次重试的 `attempt_id`。receipt 是实验事实来源；报告不得仅依赖日志文本推断执行行为。

任务公开状态固定为：

```text
pending
running
succeeded
failed_retryable
failed_terminal
exhausted
```

任务领取必须在一次原子操作中由 `pending` 或 policy 允许的 `failed_retryable` 直接进入 `running`，并写入 lease owner、expiry 与 fencing token；`claimed` 不是公开状态。过期 owner 的迟到结果只能记录为 orphaned attempt，不得改变 task 或候选结果。

### 10.5 Pair 与候选结果身份

```json
{
  "pair_id": "pair-id",
  "base_run_id": "run-stopped-at-stage2",
  "frozen_stage2_snapshot_sha256": "...",
  "single_execution_id": "...",
  "isolated_execution_id": "..."
}
```

对照与 shadow execution 必须分别保存 execution-scoped candidate Claim、EvidenceCard、SourceSnapshot、counter result、assessment 和 candidate `Stage3Result`。worker/provider 只能提交 candidate output 与 lineage receipt；它们无权写 canonical domain state。

只有经过授权的 deterministic coordinator/materializer 可以执行一次显式 promotion，将一个已验证 execution 的候选结果写入 canonical Stage 3 并推进 `RunStatus`。默认对照实验不 promotion；同一个 `base_run_id` 不得由两个 execution 自动争写正式结果。

## 11. Hook 与确定性门

本产品中的 Hook 是生命周期自动控制点，不是额外 Agent。两种执行模式必须执行完全相同的 Hook。

### 11.1 Pre-task Hook

- 验证 run status、task identity 和 input hash；
- 领取任务 lease，防止重复 owner；
- 检查全局及 task 预算；
- 构造最小 TaskPacket；
- 记录 attempt start receipt；
- 对 BlindCounterTask 执行禁止字段扫描。

### 11.2 Post-provider Hook

- 持久化 provider request；
- 保存 raw response bytes、trace ID、时间与 SHA-256；
- 验证 HTTP/transport、日期窗口和成本；
- 解析并验证结构化 response；
- 解析失败不得伪装成“没有证据”。

### 11.3 Post-model Hook

- 验证 schema；
- 拒绝 case binding 之外的 evidence ID；
- 拒绝模型编写 rating/state；
- 验证 exact quote 与 source version/offset/hash；
- 验证 `fact`、`inference`、`open_question` 类型；
- 仅在通过后物化 Claim/EvidenceCard。

### 11.4 Error Hook

- 归类稳定错误码；
- 按任务而不是整轮决定重试；
- 保存失败 receipt；
- 不得删除已成功任务；
- 超过预算或尝试次数后显式进入 `exhausted`。

### 11.5 Pre-score Gate

- 当前迭代全部预期 task 必须有终态；
- `succeeded` 结果必须完成持久化与 lineage reconciliation；
- 缺失证据保持 unresolved；
- 只有确定性 scorer 可以产生 ordinal score/state。

## 12. 功能需求

### PRD-TC-EXEC-001 — 可切换执行模式

系统必须通过服务端配置选择 `single` 或 `isolated_workers`，而不改变外部 Theme Chokepoint 研究请求和最终 Stage 3 contract。

验收边界：

- 未知模式 fail closed；
- execution mode 写入 execution record 与每个 task receipt；
- 默认生产模式在实验完成前保持现状；
- 模式切换不得改变评分合同或 source policy。

### PRD-TC-EXEC-002 — 稳定任务计划

同一个冻结 Stage 3 输入必须产生相同、有稳定顺序的 EvidenceTask 集合。

验收边界：

- task key 由 mode-independent canonical 内容派生，task ID 由 `execution_id + task_key` 派生；
- completion order 不改变合并顺序；
- proof-eligible paired execution 使用同一个预先封存的 strict-replay plan，task key 集合与顺序必须一致；
- dynamic shadow 的 planner 差异使 pair 对 topology-only 结论无效，而不是被解释为 Agent 能力差异。

### PRD-TC-EXEC-003 — 有界并行

`isolated_workers` 必须允许独立 EvidenceTask 重叠执行，但并发、来源、token、时间与成本均有边界。

验收边界：

- `max_concurrency >= 1` 且有服务端上限；
- 实际峰值并发可观测；
- 全局预算不能被每个 worker 分别完整消费；
- 并发 provider session 不得共享非线程安全实例；
- 串行配置 `max_concurrency=1` 仍产生合法结果。

### PRD-TC-EXEC-004 — 上下文隔离

multi worker 只能接收其 TaskPacket 和完成任务所需的最小治理上下文。

验收边界：

- worker 无权读取其他 task 的对话历史；
- payload 记录大小和 hash；
- source access 仍受相同 policy 约束；
- 隔离不允许丢失 run scope、日期或评分事实字段定义。

### PRD-TC-EXEC-005 — 盲审反证

multi critic 必须在看不到主研究分数和结论的情况下执行三路线反证搜索。

验收边界：

- 禁止字段扫描 100% 通过；
- critic `worker_id` 与 primary evidence `worker_id` 不同；
- multi critic 的 `context_blind=true`，且 receipt 能证明未复用 primary transcript/context identity；
- `unknown` 不能被解释为 `explicit_negative`；
- 未找到反证必须附带执行路线与 receipt；
- critic 不能直接改变 deterministic score。

### PRD-TC-EXEC-006 — 故障隔离

一个 task 的 provider、模型、解析或验证失败不得抹除其他 task 已提交的成功产物。

验收边界：

- 每个 task 独立终态；
- Stage 3 根据缺口和预算决定 incomplete，而不是静默成功；
- 恢复时只重新领取 retryable/exhausted-policy-permitted task；
- 已成功、输入 hash 未变化的 task 不重复付费执行；
- task 重试仍绑定原 run、iteration 和 input snapshot。

### PRD-TC-EXEC-007 — 输出合同等价

single 与 multi 必须产出相同类型的 Claim、EvidenceCard、SourceSnapshot、counter receipt、assessment 和 Stage3Result。

验收边界：

- 下游 Stage 4 不需要按 execution mode 分支；
- Stage 5–7 不感知 worker 数量；
- 所有现有 Stage 3 validator 对两种模式生效；
- mode-specific 调度信息只进入 execution receipt/metrics，不污染评分语义。

### PRD-TC-EXEC-008 — 配对比较

系统必须能够对同一冻结输入产生 single/multi paired execution 并生成机器可读比较报告。

报告至少包括：

- pair validity 与所有比较身份；
- task 集合和状态差异；
- wall-clock、累计 worker time、峰值并发；
- token、调用次数和成本；
- context payload 分布；
- evidence/claim 数量、来源级别和重复率；
- 三路线反证覆盖；
- unresolved、conflicted 和最终状态差异；
- validator 与现有 Golden/Holdout gate 结果；
- proof limitations。

### PRD-TC-EXEC-009 — Stage 4 第二工作包

在 Stage 3 验收后，Stage 4 必须增加以下对照：

```text
single:
  一个共享上下文完成 company evidence、assertion 和 critique

isolated:
  producer 生成 company assertion；独立 verifier 根据原始 source bytes 验证；
  独立 critic 检查 challenger/replacement/earnings 反证
```

验收边界：

- verifier `worker_id` 不得等于 assertion producer `worker_id`；
- verifier 只根据 assertion、assessment scope 和原始 source context 判定；
- Stage 3 company-neutral evidence 不得直接迁移为 Stage 4 company score credit；
- Company Assessment incomplete 继续阻止 Stage 5；
- Stage 4 对照不改变既有 company scoring contract。

## 13. 非功能需求

### 13.1 一致性与幂等

- Task planning、task claim、attempt terminal transition 和 materialization 必须幂等；
- 同一 `execution_id + task_key` 最多有一个成功逻辑结果；
- 重复 provider response 不得生成重复 evidence；
- 并发完成顺序不得改变稳定输出排序或 hash。

### 13.2 可恢复性

- Orchestrator 或 worker 崩溃后可从 durable task state 恢复；
- 过期 lease 可重新领取，但前一 owner 的迟到提交必须被 fencing token 拒绝；
- 已提交阶段 receipt 不因后续失败消失；
- 不允许从日志猜测恢复状态。

### 13.3 成本与资源控制

- 默认实验 `max_concurrency` 建议为 4，最终由部署配置限定；
- 总预算属于 execution，paired fairness gate 要求两边上限相同；预算不属于每个 worker；
- cost、token、provider calls 和重复工作率必须报告；
- 在真实来源上的 multi shadow run 默认关闭，需显式启用；
- 多 Agent 成本未被评估前，不得设为生产默认。

### 13.4 安全

- 抓取内容视为不可信数据，不得成为系统或工具指令；
- worker 只获得完成任务所需的工具和 source scope；
- credential、完整私有文档和其他 task payload 不得进入模型日志；
- task/error receipt 不保存 secret-bearing request header。

### 13.5 可观测性

每个运行至少暴露：

```text
mode
pair_id
base_run_id
execution_id
planned / succeeded / failed / exhausted task counts
peak_concurrency
wall_clock_ms
sum_worker_ms
input/output tokens
provider calls and cost
context payload percentiles
evidence and counter-evidence counts
retry counts
final Stage 3 status
```

## 14. 实验设计

### 14.1 Phase A：确定性架构验证

使用 fake provider、fake worker 和可控 clock，不调用 live provider。

必须证明：

1. 同一 strict-replay 输入生成相同 task key 集合和顺序；
2. single 的最大 active worker 为 1；
3. multi 在 `max_concurrency > 1` 时真实出现任务重叠；
4. multi 峰值并发不超过上限；
5. completion order 不改变结果顺序；
6. 注入一个失败时其他成功结果仍存在；
7. resume 只重试失败任务；
8. BlindCounterTask 不含禁止字段；
9. unknown counter coverage 不能提升为 explicit negative；
10. 两种模式均通过相同 schema、lineage 和 deterministic scoring 检查。

Phase A 证明执行性质，不证明模型质量或 live provider 行为。

### 14.2 Phase B：冻结语料配对评估

使用相同 frozen evidence/source snapshots，优先复用仓库已有 HBM/advanced-packaging Golden、AI data-center power/cooling cross-theme case 和版本化 Holdout。

Phase B 必须使用在两臂启动前封存的 `strict_replay` 完整任务计划；任何 arm 都不能根据自身中间结论增删该计划。这样得出的差异才可归因于执行拓扑、上下文和模型输出，而不是自适应 planner 分叉。

控制变量：

- 相同底层模型版本和推理参数；
- 相同 prompt family；
- 相同 task plan 和 source snapshot；
- 相同 run budgets；
- 多次重复运行以披露模型方差；
- evaluator 不得看到 mode label，除非评估系统指标。

Phase B 不预设 multi 质量更高。必须如实报告：持平、改善、退化或样本不足。

### 14.3 Phase C：Shadow 运行

只有 Phase A 完成且 Phase B 未发现阻断性退化后，才允许对真实研究请求进行 paired shadow。

- 用户可见结果继续来自现有模式；
- shadow 结果不推进 production status，不进入交易或量化消费；
- live provider 成本必须有独立预算与开关；
- shadow failure 不得影响主运行；
- 两边 candidate result 保持 execution-scoped；未经显式 promotion 不写 canonical Stage 3 或推进 `RunStatus`；
- 若允许 production-like dynamic planning，必须报告 plan divergence；发生分叉的 pair 不得给出 topology-only 胜负结论；
- 报告必须区分 deterministic、frozen-corpus 和 live-provider 证据。

## 15. 指标与成功标准

### 15.1 必须通过的架构门

| 指标 | 成功标准 |
| --- | --- |
| Pair input integrity | 所有强制比较身份一致；否则 `PAIR_INVALID` |
| Task plan equivalence | proof-eligible paired execution 的 task key 集合和顺序 100% 一致 |
| Bounded concurrency | 无一次实际并发超过服务端上限 |
| Real overlap | deterministic fan-out fixture 中 `peak_concurrency >= 2` |
| Blind-packet integrity | 两种模式 100% critic payload 不含禁止字段 |
| Blind-context integrity | multi critic 100% 使用不同 `worker_id`、fresh context 且不含 primary transcript；single 明确报告 `context_blind=false` |
| Identity separation | multi critic/verifier identity 与 producer identity 100% 不同 |
| Failure containment | 单任务故障不丢失其他已提交成功结果 |
| Targeted resume | 未变化的成功任务重复执行数为 0 |
| Contract equivalence | 两种模式均通过相同 Stage 3 schema/lineage/scoring validators |
| Truthful incompleteness | 缺失或失败任务不能被归一化为完成 |

### 15.2 需要报告但不预设胜负的指标

- wall-clock latency 与累计 worker time；
- input/output token、模型调用和 provider 调用；
- 总成本与每个有效 EvidenceCard 成本；
- 每个 task 的 context payload 大小；
- 一手来源占比、去重后 evidence 数、无关证据率；
- counter-evidence found / explicit-negative / unknown 分布；
- unresolved 和 conflicted 比例；
- Golden/Holdout hard-gate、final-state、pairwise ranking 和特殊集结果；
- 跨重复运行方差。

### 15.3 质量声明边界

允许的结论示例：

```text
在冻结输入 X、模型 Y、并发 4、样本 N 下，isolated_workers 将 p50
wall-clock 降低 Z%，一个 worker 故障未删除其他成功结果，且现有质量门未观察到退化。
```

禁止的结论示例：

```text
多 Agent 更聪明。
多 Agent 已普遍提高金融研究准确率。
一次 demo 证明了独立 critic 总能消除确认偏误。
```

## 16. 用户体验与接口

首期不改变面向研究用户的 Theme Chokepoint 输出格式。开发/评估接口增加：

```text
execution_mode: single | isolated_workers
pair_id: optional string
base_run_id: string
execution_id: server-generated response field
max_concurrency: server-bounded integer
shadow: boolean
```

运行状态增加内部可观测字段，但不将本地路径、credential 或任意模型参数交给外部调用者控制。

配对报告建议输出：

```text
reports/theme_chokepoint_agent_ablation/<pair_id>/
  pair-manifest.json
  single-summary.json
  isolated-workers-summary.json
  task-diff.json
  quality-gates.json
  report.md
```

报告路径和 schema 在系统设计阶段冻结；本 PRD 只冻结所需内容。

## 17. TDD 与验证要求

所有 runtime、contract、persistence、retry 和 orchestration 修改必须遵循仓库 `AGENTS.md` 的严格 TDD 流程。

每个独立 invariant 必须记录：

```text
SELECT INVARIANT
WRITE TEST
VERIFY RED
IMPLEMENT
VERIFY GREEN
REFACTOR
REGRESSION
SCOPE CHECK
RECORD EVIDENCE
```

优先测试工作包：

1. stable task planning；
2. sequential executor；
3. bounded fan-out executor；
4. stable merge ordering；
5. task receipt persistence；
6. one-worker failure containment；
7. targeted resume and fencing；
8. blind critic packet 与 fresh-context/worker-identity contract；
9. shared budget enforcement；
10. paired-run validity；
11. existing Stage 3/4 regression；
12. frozen Golden/Holdout evaluation。

本地单元测试不证明外部 provider、生产部署、真实成本或用户可见研究质量。

## 18. 实施工作包

### WP0 — 合同与基线

- 冻结 v1.0 TaskPacket、TaskOutcome、ExecutionReceipt 和 pair manifest schema；
- 捕获现有 Stage 3 回归基线；
- 记录当前顺序运行的任务数、延迟、成本和质量指标；
- 不修改模型 prompt 或评分合同。

### WP1 — 框架无关 Sequential Executor

- 从 Stage 3 内循环提取 task planner 和 executor seam；
- 保持默认行为为顺序执行；
- 证明现有结果合同不变；
- 新增 task-level receipt。

### WP2 — Bounded Isolated Worker Executor

- 实现有界 fan-out、稳定合并、共享预算和 provider 生命周期；
- 实现 worker 最小上下文；
- 实现任务级失败和恢复；
- 完成 Phase A 并发与故障测试。

### WP3 — Blind Counter Worker

- 定义 BlindCounterTask；
- 去除 critic 对 provisional score/state/summary 的依赖；
- 保留三路线、raw response、materialization 和 reconciliation；
- 完成盲审输入和 unknown/explicit-negative 测试。

### WP4 — Paired Evaluation

- 实现 pair validity 和比较报告；
- 执行 frozen-corpus 重复评估；
- 记录质量、成本、延迟和方差；
- 决定是否进入 shadow。

### WP5 — Stage 4 Independent Verification

- 在 Stage 3 验收后单独实施；
- 对照共享上下文 producer/critic 与独立 producer/verifier/critic；
- 不改变既有 company scoring contract；
- 复用 production 中现有独立 endpoint、credential 和 worker identity 要求。

### WP6 — 框架选型与适配

- 仅在 WP0–WP4 合同稳定后执行；
- 对 Mastra、LangGraph 和薄 Python executor 做同一套能力打分；
- 框架只能实现 executor/orchestration adapter，不拥有领域评分语义。

## 19. 框架选型门槛

后续框架比较必须以本 PRD 的真实需求为准：

| 能力 | 必需程度 |
| --- | --- |
| typed state / structured task payload | 必需 |
| fan-out / fan-in 与 bounded concurrency | 必需 |
| durable checkpoint / resume | 必需 |
| task-level retry 与 idempotency | 必需 |
| fresh/isolated worker context | 必需 |
| tracing、token、cost 和 latency telemetry | 必需 |
| stable deterministic merge | 必需 |
| Python 现有领域对象适配成本 | 必需评估 |
| provider/tool permission isolation | 高优先级 |
| human interrupt | 可选；当前永久 human-gate policy 不得被框架默认行为改写 |
| JavaScript/TypeScript UI ecosystem | 可选，根据交付界面决定 |

如果薄 Python executor 已满足全部 MVP 门槛，采用 Agent 框架不是产品验收条件。

## 20. 风险与缓解

| 风险 | 缓解 |
| --- | --- |
| 把多 Agent 数量当作质量 | 固定底层模型、输入、预算和评分合同；质量单独评估。 |
| multi 获得更多搜索预算 | 使用 run-level shared budget 与 pair validity。 |
| 并发产生重复来源或重复付费 | content-derived task key、execution-scoped task ID、source identity 去重和 idempotent materialization。 |
| critic 虽独立但仍看到主结论 | BlindCounterTask 禁止字段 + payload receipt + 测试。 |
| 隔离上下文丢失必要 scope | TaskPacket 强制包含 region/as-of/horizon/policy/hash。 |
| worker 迟到覆盖新 owner | lease + fencing token。 |
| completion order 导致结果不稳定 | 按 planner stable order 合并。 |
| multi 更快但更贵 | 同时报告 wall-clock、累计 worker time、token 与成本。 |
| 单次 demo 被过度解读 | 分离 deterministic、frozen-corpus、shadow proof boundary。 |
| Agent 框架侵入领域语义 | 框架仅实现 executor adapter，scorer/validator/repository 继续由现有 Python 领域层拥有。 |

## 21. 发布与回滚

1. `single` 保持默认，直到 Phase A、Phase B 和 shadow gate 明确通过；
2. `isolated_workers` 首先只允许测试和显式 shadow；
3. 新 task/receipt 表或字段必须做前向兼容 migration；
4. 回滚只需关闭 multi mode；已有 receipt 和 evidence 不删除；
5. execution mode 不得改变历史运行解释；
6. 未完成 paired execution 必须显示 incomplete/invalid，不得生成获胜模式结论。

## 22. 交付物

- 本 PRD；
- execution/task/pair 数据合同；
- Stage 3 framework-neutral system design；
- SequentialExecutor 与 IsolatedWorkerExecutor；
- BlindCounterTask 与独立 critic adapter；
- task ledger/migration；
- deterministic architecture test suite；
- frozen-corpus paired evaluation runner；
- pair comparison report；
- Mastra/LangGraph/薄 Python executor 选型 ADR；
- TDD RED/GREEN/regression evidence。

## 23. 已决定事项

1. Stage 1 使用一个逻辑 Agent；接口可分，但默认不拆 execution identity。
2. Stage 2 默认使用一个逻辑 Agent；首期不做节点 fan-out。
3. Stage 3 是首个 single/multi 对照实验边界。
4. Stage 3 multi 的核心是 evidence task fan-out 和 blind counter worker。
5. Stage 4 独立验证属于第二工作包。
6. Stage 5 和 Stage 7 由确定性代码拥有。
7. Stage 6 首期保持单 evaluator + 确定性调度。
8. 所有模式共享 evidence、scoring、validation 与 persistence contracts。
9. 多 Agent 的验收目标是并行、隔离、独立复核和故障隔离，不是“更聪明”。
10. 框架选型在任务合同与 Phase A 验证之后进行。

## 24. 延后到框架选型阶段的决策

以下问题不属于本次产品冻结边界，必须在 WP6 ADR 中回答；其答案不得静默修改本 PRD 已冻结的任务、隔离、公平性或失败语义：

1. Mastra、LangGraph 和薄 Python executor 哪个最自然地承载现有 Python domain/repository？
2. fresh context 的可验证边界是新模型请求、新进程、独立 credential，还是其组合？
3. task checkpoint 应由框架存储还是继续完全由 `ThemeChokepointRepository` 拥有？
4. 分布式 worker 是否属于首个生产版本，还是只采用单进程 bounded concurrency？
5. provider 限流、全局预算和 fencing token 的最终 owner 是谁？
6. telemetry 如何避免保存敏感来源全文和 credential？

## 25. 批准与证明边界

用户于 2026-09-04 批准并冻结本 PRD。该批准表示：

- 可以按 WP0–WP5 规划和实施 single/multi 可控实验；
- 可以新增 framework-neutral task、receipt 和 pair contracts；
- 可以在不改变评分语义的条件下重构 Stage 3 执行 seam；
- 可以使用冻结 fixtures 和已授权预算进行明确标记的评估。

该批准不表示：

- 选择或安装 Mastra/LangGraph；
- 授权生产部署或新的外部 provider 成本；
- 声称多 Agent 普遍提升研究质量；
- 修改任何冻结评分、Golden、Holdout 或 release gate；
- 授权交易、投资结论或自动执行。

本 PRD 的产品决策、功能需求、验收边界、Stage 责任划分、公平性约束、质量声明边界及证明边界构成 v1.0 冻结内容。任何改变这些含义的修改必须发布新的产品版本、重新评审，并生成新的 freeze manifest；排版、链接修复和不改变语义的澄清仍须更新制品哈希，不能静默覆盖冻结制品。
