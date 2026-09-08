# Stage 3 Codex Sessions System Design / ADR / Migration

Status: READY_FOR_HUMAN_REVIEW

Version: 1.0-candidate.1

Date: 2026-09-07

配套：[PRD](../product/theme-stage3-codex-sessions-prd-v1.md)、[API](../api/theme-stage3-codex-sessions-api-v1.md)。本文件含实现所需 ADR 与 Migration Contract。没有冻结或运行授权。

## 1. 决策与真实基线

ADR-CS-01：Coding 编排唯一选用公共平台的 LangGraph，入口 http://localhost:4130/ui。平台继续使用公共不可变 Prompt、Registry、Frozen Bundle 校验和 Developer-only 写入流程；不修改平台引擎实现或现有角色的 ephemeral 策略。产品的 Stage 3 runtime 使用现有 Python seam 和新增 Codex adapter，不引入第二套 LangGraph/Mastra。

ADR-CS-02：以六个 lane 为 session/用量边界，task 是 lane 中一个有界 research/assess turn。多个 lane 并发，每个 lane 串行。选择 lane 内复用 session 是为了保留维度内研究上下文，不宣称它是独立评审。节点资料必须带作用域，不能根据会话历史直接跨节点引用证据。

ADR-CS-03：使用本机官方 Codex App Server 的 stdio 协议，初始支持本机实测 `codex-cli 0.149.0`；能力前置检查不通过就关闭新特性。不是 HTTP 访问 ChatGPT 私有接口，不解析 auth.json，不复制登录 token。服务器启动必须隐藏窗口并绑定本地通道；不公开 WebSocket endpoint。

ADR-CS-04：订阅用量不伪装成实际美元成本。六份 lane budget 为逻辑配额，账号 quota 仍共享。Token 为可观测软上限，不能保证严格截断远端计费。

当前代码基线：

- `stage3.py::run()` 仍是顺序、canonical 写入路径。
- `stage3.py::run_candidate()` 与 `stage3_rpc.py` 是独立数据库的本地 RPC 试验；使用 execution 级共享美元预算，不具备 Codex session 或六 lane 配额。
- `providers/scorer.py::DeepSeekV14SegmentScorer` 会发模型调用；新路径不能原样复用其网络调用，否则绕过六 lane 用量限制。
- `_validate_candidate`、`_materialize`、`_validate_segment_draft_evidence`、维度验证和 `_finalize_assessment` 可作为共享的确定性边界；修改应保留 legacy tests。
- 当前 scoring overlay 未默认开放；本特性绝不通过修改评分 JSON/hash 或自动设置 controlled overlay 标志绕过治理。

现有 v1.0 ablation 和 v1.1 候选文档不构成本特性的语义父合同；只保留兼容边界，避免不完整地继承历史 migration/paired-evaluation 范围。

## 2. 组件、信任及状态所有权

| 组件 | 权威责任 | 禁止 |
| --- | --- | --- |
| SessionResearchCoordinator | 六 lane 规划、轮询、轮次、停止、稳定合并 | 生成未经验证的事实或自动推广 |
| LaneBudgetLedger | task/turn admission、时间、usage、来源计数、fencing | 把账号 usage 差额硬分给各 lane |
| CodexSessionAdapter | initialize、thread/turn 生命周期、用量事件、原始回执 | 修改登录方式、买额度、盲重发不确定请求 |
| SourceVerifier | 按冻结 source policy 独立取原文、校验来源/引用/scope | 相信模型自报 original_text 或搜索摘要 |
| DimensionProposalValidator | 校验模型维度提案，复用当前 v1.4 验证器 | 把模型输出直接当最终评分或换评分合同 |
| CandidateFinalizer | 原验证器检查、确定性汇总、持久候选结果 hash | 调用有副作用的 legacy scorer/critic/relief |
| SessionCleanup | 归属验证、无活动 turn 核对、归档与取消订阅 | delete、rollback、主动 compact、归档别的任务 |

公共 Coding run 状态、产品 execution 状态、Codex thread 状态分离。LangGraph checkpoint 不是产品预算数据库，不具有 canonical 授权；所有产品模型调用从新的 adapter 进入。

研究 session 的工作目录为单独的本地 execution/lane 空目录，不是源代码仓库或共享 Prompt 目录。使用只读 sandbox、显式允许的研究工具策略，禁止 shell 写入、子 Agent、任意插件、收费 API、跨 lane 文件读取和 workspace publication。认证由 Codex 自己管理。若本机版本无法施加并验证此策略，报 CAPABILITY_UNAVAILABLE，不降级为 unrestricted。Provider/model 实际身份、CLI 版本、工具策略 hash 和评分 hash 在创建时冻结。

## 3. 协议前置检查与研究循环

### 3.1 能力基线

检查本地 binary 版本、initialize 握手、`account/read` 登录类型、thread/start/resume/read/list/archive/unsubscribe、turn/start/interrupt 与 thread/tokenUsage/updated schema。无需发送研究 turn 即可验证的能力应先验证；不支持的方法必须在离线协议 fixture 或另行授权的 smoke 中证实，不能声称仅版本号就证明完整功能。

使用已有 ChatGPT 认证，认证不符返回 AUTH_MODE_UNSUPPORTED。不读取凭证文件；禁止 API fallback、外部 token 注入、credit/reset 操作。create 通过只读 config/read 和 model/list 解析服务器当前默认 model/provider/effort，将明确值与版本写入快照；无法无歧义解析则 CAPABILITY_UNAVAILABLE，不为解析默认创建 thread。后续 thread/start/turn/start 显式传入冻结值，实际返回身份不匹配则 SESSION_DRIFT，零后续派发；用户请求不可随意指定 provider、endpoint、credential、文件路径或不同模型。

thread/start 使用 ephemeral=false、只读 sandbox、固定服务标识及 lane 工作目录；thread/name/set（本机能力确认后）设置可识别标题。产品不 fork 当前 session、不自动创建第七个 reviewer session。禁用 delegate/subagent 工具；若发现意外子任务，暂停并报告，不能自动归档整棵未经核对的树。

### 3.2 调度单元

固定六维顺序见 PRD；节点按 node_id 字典序。research 与 assess 分开为 task_kind，phase 顺序 research 在 assess 前。v1 的 material_field 只接受六个 dimension_id；其他 field 映射不能猜，前置返回 TASK_MAPPING_UNSUPPORTED。

每个 lane 的 round：对本轮缺口节点逐个 research，验证并保存后按相同节点顺序 assess。若同一节点已有有效 proposal 且本轮无新增已接纳证据，则不重复 assess。轮尾收集该 lane 的 missing questions；下一轮只处理未解决的节点，直到无缺口或某个 lane 上限触发。其他 lane 不必等待本 lane 轮尾才能继续；跨维的总评分只在汇合边界计算。

全局选 lane 使用轮询，跳过无待办、已有活动 turn、暂停或终态的 lane。第一轮非空 lane 的首个 task 先于已服务 lane 的第二个 task；因活动 turn、账号限制或缺证据造成的暂不可运行不阻止其他 lane。每 lane 内节点轮询；同一节点/维度/round/kind 只允许一个 logical task。

### 3.3 执行与验证

1. 在短事务内校验 lane/task 状态、配额、截止和 execution writer lease；生成 attempt/fence、扣占额度与 dispatch_intent。
2. 恢复/创建该 lane 的线程；确认未有未知或外部活动 turn；发送一个有界 turn。turn/start 成功返回仅证明接收，不证明研究完成。
3. 持续读取 turn、item 和 usage 事件。记录 thread_id/turn_id；重复通知幂等处理；在途可先保存 raw item，但不提前把半截回复当成功。
4. research 最终 JSON 先通过严格 schema。模型原文仅为 proposal；SourceVerifier 重新获取其引用的原始 URL，绑定 content hash、抓取时间、日期来源和引用位置，替换不可信原文字段后执行现有验证。
5. SourceVerifier 只允许既有 source policy 允许的公开 HTTP(S)来源；拒绝本地/私网/凭证 URL，限制重定向、解析后的目标地址、响应字节数和 deadline。不启动 Tavily 等付费检索；Codex 内置搜索也只提供线索，仍要独立获取原文。max_source_documents 定义为本 lane 来源获取 admission 次数上限，计数名 source_fetches_admitted：请求发送前扣占，失败或无 quote 不退还；一次受限重定向链算一次，不允许隐式重试。仅同 lane 已持久化且满足冻结时效策略的 cache 命中免扣；v1 不跨 lane 共享 cache，各自扣占并保留原文快照引用。
6. assess turn 只收到本节点、本维度已接纳 evidence IDs、claims 和当前冻结合同要求，返回 v1.4 维度提案。复用 `providers/scorer.py` 的纯校验部分，不实例化其网络 client。新提案失败时，按 API §3.3 的 evidence basis 键核对旧提案且重验；仅仍适用者可沿用，否则使用既有 unknown 构造。最终六维组合仍经 Stage 3 scope/ownership/weight/gate 验证。
7. 通过 CAS 后，accepted artifacts、task terminal state、turn receipt 和本次 usage checkpoint 原子可见；每项完成即可读取，不等待所有 lane。
8. lane 结束先写结果/原因，再进入 SessionCleanup。最后候选合并使用 node_id、固定 dimension 顺序、round、kind、task_id 的稳定顺序；canonical run 不动。

Worker、SourceVerifier 和 scorer 都不能向用户自报美元成本。新 candidate DTO 的 actual_cost_usd=null；不得把它转成 legacy Stage3Result.cost_usd_spent=0 然后称其免费。候选 envelope 承载领域 assessments/evidence 和独立 usage sidecar；legacy canonical DTO 不变。

## 4. 独立账本与上限

所有计数为非负整数。limit 在 execution 创建时不可变，无自动续杯。max_tasks/turns/rounds/source_documents 为 admission 边界硬约束；墙钟截止是停止新工作和请求中断的边界，不保证供应商在同一毫秒停止执行。Token 仅为软上限。

原子 admission 同时满足：lane 未终态、无未知/活动 turn、task 可运行、全局 active_turn_count < max_concurrency、tasks_admitted（若新 task）+1 <= max_tasks、turns_admitted+1 <= max_turns、round <= max_rounds、当前时间 < lane_deadline、usage 可用且未达 token_soft_limit。失败只影响本 lane；全局账号限流除外。

active_turn_count 是可能在途 turn 的持久槽位数，从 dispatch intent admission 起计（包括该 attempt 的 thread 创建），直到确认 turn terminal 或确认从未发送；不只数 received running 事件。SourceVerifier 每 lane 至多一个 fetch，全 execution 至多 max_concurrency 个 fetch；每 lane 的生命周期 mutation 串行，全 execution 至多六个，由 coordinator 发起，不占用已经释放的 turn 槽。SQLite 提交串行，诊断只读查询不纳入 turn 限额。以上是不同资源池，不能把 max_concurrency 称为所有后台操作总并发数。

计时从第一次 admission 起，持久 started_at/deadline_at；创建 session、排队、离线时间计入。使用 UTC 持久截止并在进程内以 monotonic 约束等待；检测明显时钟回拨时暂停，不允许增加剩余预算。attempt deadline=min(lane deadline, admission time+turn_timeout_seconds)。

Token 更新以 thread 累计 tokenUsage.total.totalTokens 的高水位计数；新 thread 基线为 0，恢复沿用已存高水位。相同/较小的迟到观测不扣回已计值；同一 thread 的 last 只供诊断，cachedInputTokens/reasoningOutputTokens 不再次累加。若确认 provider 计数发生 reset、出现负数或字段不能解释，usage_state=unknown，暂停新 turn；不根据账号全局额度差值估计该 lane 用量。

已收到最终输出而缺少最后用量事件时，可保存已验证证据，但 task 保持 awaiting_usage、lane 暂停。等待上限为 turn_timeout_seconds（不延长 lane deadline），之后按 USAGE_UNAVAILABLE 结束该 lane，保留最后已观测值及 uncertainty。usage API/event 回放可恢复等待中的状态；达到终态不再补开研究。

软额度达到时立即禁止新 turn，对在途请求 interrupt；继续记录超额观测。账号 rate limit 是 account-paused，所有 lane 不再 admission；显式 continue 才重查额度并恢复，deadline 仍推进。无轮询自动买 credits、reset 或换账号。实际第三方费用不可确认时永远是 unavailable。

## 5. 状态、恢复与失败隔离

详细枚举见 API §4。两个状态轴分别表达研究结果与 session 清理，不能把 cleanup_failed 改写成研究成功/失败。

task：pending -> dispatching -> running -> succeeded | failed；dispatching 回应不确定变 outcome_unknown；可恢复 usage 等待为 awaiting_usage。失败可按允许原因在同 task 内创建新 attempt，直到 max_attempts_per_task 或 lane 额度耗尽。仅 PROVIDER_TIMEOUT/PROVIDER_TRANSPORT_ERROR 且已确认旧 turn 终止可自动重试。research envelope 结构非法或 assess 提案结构/语义非法为 task terminal failed；research 单项来源/作用域/引文错误仅拒绝该项，保留通过项和 findings。结构有效但空或全拒为 succeeded + evidence_outcome=unknown；all_rejected 与真正 empty 分别记录，不能解释为已证明不存在证据。详细拒绝规则见 API §3.2。

lane 终态优先级：不可恢复协议/数据错误 -> failed；未解决 task 因额度或时间关闭 -> exhausted；用户取消 -> cancelled；否则 -> completed。completed 可以包含未知证据和未知维度评分。所有 contributing reasons 都保留。某节点缺少有效 assess 则使用 unknown 维度，不丢弃其他维度结果。

execution 等所有 lane 研究终态或显式 account/manual pause。六 lane 终态后，候选最终验证不通过则 execution=failed、error=FINALIZATION_FAILED，任务产物仍保留；验证通过且所有 lane completed 则 completed；存在 failed/exhausted lane 则 degraded；用户取消则 cancelled。清理未完成独立呈现，不阻止已合法产物读取，不允许声明整个交付已完成。

cancel_execution 先持久化 cancellation intent，execution=cancelling，禁止所有新 admission；已有终态 lane 不改写。活动 turn 请求 interrupt，未确认终止的 lane=paused、session=termination_unknown、cleanup=waiting_terminal 并保留槽位。仅确认终止（或证明未发出）后，未终态 lane 按上述原因优先级进入终态并允许 cleanup=pending；所有 lane 达终态后 execution=cancelled，最终验证失败优先报告 failed/FINALIZATION_FAILED。cleanup waiting_terminal 绝不发送 archive。

每个 execution 只有一个协调器可派发。单机 SQLite 事务管理 writer lease：owner_id、monotonic fence、lease_expires_at；heartbeat 周期 <= lease_duration/3，lease_duration 配置 30 秒。恢复只能在旧租约过期后 CAS 接管，旧 owner 不得继续 admission 或提交结果。若已有活动/未知 Codex turn，恢复仍计入并发槽，不因本地进程死亡假设释放。

**外部调用不存在本地 exactly-once 保证：**

- dispatch_intent 已落盘但尚未发出时崩溃，与发出但丢 ACK 可能不可区分。保守消耗 attempt 额度，先用 thread/read/turn identity 对账；不能盲重发 turn/start。
- thread/start 丢 ACK 时记 session=creation_uncertain，禁止创建第二个 session。可通过创建 intent 的独立 cwd/时间范围进行只读查找，但候选匹配不等于所有权证明；有歧义则 NEEDS_SESSION_RECONCILIATION。v1 不提供手动认领映射接口；运维修复连接后可 continue 重做只读对账，仍不能证明则保持暂停，人工认领需要后续合同。不得猜 ID 或删除疑似孤儿。
- turn/start 丢 ACK：原 session 可核对到唯一匹配的 task/attempt marker 时收养该 turn；否则 outcome_unknown。clientUserMessageId（若使用）仅为相关性，不被视为服务器幂等承诺。
- 已成功 task 不再发送；新 owner 只补 pending/明确可重试任务。旧 turn 迟到输出进入 orphan receipt，不能覆盖新 attempt 或再次累计同事件。
- 发生 interrupt：先等待 turn/completed 或 thread/read 确认终止；未确认时保留槽位、lane=paused、TURN_TERMINATION_UNKNOWN，其他空槽仍可工作。不能用杀本地客户端等同取消远端模型。
- 人工在受管 session 输入内容、模型/工具策略变化或未知外部 turn 被发现时，暂停为 SESSION_DRIFT，不把外部费用或回复自动纳入正常研究。

## 6. 清理和归档

数据事务与外部归档不能原子提交，使用持久 cleanup intent，不使用“先归档再补结果”。

顺序固定：确认 terminal turn -> 保存 lane artifact/失败原因及 usage -> CAS cleanup_pending -> 校验该 thread 的 ownership -> thread/archive -> thread/list archived=true + thread/read 核对 -> archived -> thread/unsubscribe -> cleanup completed。

归档 API 超时先查询归档状态再决定重试；重复归档不能生成新研究 turn。重试最多 3 次本地 lifecycle API 调用，间隔 1、2 秒；仍失败标 cleanup_failed，提供显式 retry_cleanup。后续重试不消耗研究 task/turn/Token 配额。unsubscribe 返回 notSubscribed/notLoaded 视为该操作完成；不能宣称最后订阅者或立即内存卸载。

归档会涉及潜在子线程，因此只接受禁止生成子线程且未检测到 descendant 的受管 session；否则 CLEANUP_SCOPE_UNSAFE，等待人工处理，不归档父线程以免误伤。任何日志匹配/标题包含相似文字都不足以建立归属。永不调用 thread/delete、thread/rollback、thread/compact/start；不清除原始研究记录或修改账号保留策略。

descendant 核对依赖本机 schema 的 Thread.parentThreadId / source.subAgent.thread_spawn.parent_thread_id；forkedFromId 命中亦保守视为不安全关联。归档前用 thread/list 分别 archived=false/true，显式列出所有本版本 sourceKinds（空列表默认只含交互线程，不可使用），遍历完全部 cursor，不设 cwd/title 过滤；只处理 ID/父关系等元数据，不读取无关正文、不将无关资料写入研究库。沿父关系计算闭包并核对受管 thread/read 的 turn 状态。权限不足、分页失败、来源枚举/父关系不可解释、并发 drift 均不能解释为无子线程：前置失败 CAPABILITY_UNAVAILABLE 或清理失败 CLEANUP_SCOPE_UNSAFE。离线 fixture 必含跨 cwd、非交互来源、已归档子线程和不完整分页；真实可见性仍待授权 smoke 验证。

Codex app UI 的活动列表与当前 App Server 存储一致性必须在授权 smoke 中验证。协议 archived=true 的证据只证明服务端归档，不假装已经验证桌面 UI；若桌面连接到不同 store，报 UI_STORE_MISMATCH，保留记录并停止 UI 清理完成声明，不更改用户全局配置迁就它。

## 7. 证据、身份与接口

使用 API §2 的 Python-local canonical JSON v1 规则。input_snapshot_sha256 绑定 request 去 run_id 的语义字段、排序后的 graph、source/tool policy hash、实际模型/effort、scoring/governance hash、六 lane limit、adapter protocol/version。execution_id 只作执行命名空间，不作为内容可信证明。

task_key=hash(input_snapshot_sha256,node_id,dimension_id,round,kind,sealed query/evidence IDs/schema hash)。task_id=hash(execution_id,task_key)。每次尝试新 attempt_id 和 fence。平台 Coding run 的 candidate snapshot SHA 是整个候选源码快照，**不同于**产品 candidate_result_sha256；Human Final Confirmation 必须使用平台提供的值。

同一 candidate_result_sha256 绑定排序后的 accepted evidence/claims/source hashes、验证后 assessments、所有未解决原因、评分合同及 input_snapshot_sha256；不包括 elapsed、session/attempt/receipt ID、usage、cleanup 时间。完整含 telemetry 的 artifact 另有 artifact_sha256，两者不能互换。只允许自身 execution 数据进入合并；不复用另一 lane 的评分结论。评分浮点数采用 API §2 的数值规范，不能丢弃或随意舍入。

## 8. Migration Contract

目标：新建专用 SQLite `stage3-codex-sessions-v1` 数据库，通过 profile allow-listed runtime root 定位。用户请求不接受路径。logical tables 至少有 metadata、executions、lanes、tasks、attempts、usage_events、accepted_artifacts、source_snapshots、session_intents、cleanup_intents、operation_idempotency；可物理合并，但唯一性、事务和恢复规则不能变化。

新空文件一次事务创建 schema，metadata 写 feature_id 与 schema_version=1；DDL 不使用隐式提交的迁移拼接。对于已存在非空数据库，先 mode=ro 校验 metadata、必需列/索引/唯一约束与 schema_signature；未知、缺表、部分初始化、版本错误或旧 rpc_* / theme_chokepoint_* 数据库均报 MIGRATION_REQUIRED，零写入。schema_signature 的规范为实际 v1 DDL 的表名/列类型/nullability/PK/unique/index 结构排序投影，随实现作为版本化 fixture，由 Developer 测试冻结；不能仅检查文件名。

无历史 backfill：不把旧会话、旧 Stage3Result 或共享美元 RPC ledger 自动变成六维账本。不修改旧数据库 schema/data、评分文件、原 Frozen Bundle、已注册 Profile。新库为空时才初始化；出现初始化失败保留诊断，不自动覆盖已有文件。

唯一约束：一个 execution/dimension 一个 lane 和至多一个有效 session binding；execution/task_key 唯一；attempt_id 唯一；线程使用观测原事件 hash 幂等；操作幂等键绑定完整请求 hash。每一事务提交 accepted artifact、task outcome 与 usage checkpoint，不能留下“成功但无 artifact”。

启用默认 false，legacy run()/CLI 默认行为不变。禁用后拒绝新 execution/admission，允许在途结果安全结算和 cleanup；不删除记录，不放宽预算。回退仅禁用此入口，旧库不受影响；重新启用同 execution 沿用 counters/deadline。代码版本升级必须继续读取 v1，不能重解释已消费额度。

## 9. 交付与验证

代码由公共平台唯一 LangGraph Coding run 产生。建议新 Profile `financial-agent-theme-stage3-codex-sessions-v1`，候选 Profile 不注册；冻结后才注册和启动。允许范围仅 `src/event_collector/theme_chokepoint/**`、现有 theme CLI 文件和 `tests/test_theme_chokepoint_*.py`；不改 framework/package dependency、旧治理文档、数据目录或全局 Codex 设置。

开发先提供严格离线测试：伪造 JSON-RPC 对端、用量重复/乱序、未知 ACK、旧 owner、断线恢复、坏引用、lane 独立耗尽、归档失败、原 run 零变化；fixture 不登录、不调用真实模型、不归档真实线程。回归覆盖 Stage 3、Stage 4、生产 composition、orchestrator、repository 和原 RPC。

本机真实 smoke 另行批准具体 lane、task/turn/time/token 上限及允许来源后执行；保留 auth mode、版本、实际 thread/turn ID、归档核对和 UI store 证据，不记录密钥。Coding 自动测试不能自作主张运行这项 smoke。运行到 Human Final Confirmation 只汇报 exact bundle SHA、candidate snapshot SHA、开放 P2，不自动 GO。

## 10. 外部接口证据与局限

- [Codex App Server](https://learn.chatgpt.com/docs/app-server)：thread/turn、outputSchema、usage、archive/unsubscribe，以及实验性边界；2026-09-07 实际读取。
- [Authentication](https://learn.chatgpt.com/docs/auth)：ChatGPT 与 API key 的认证/计费区别；本机只读检查为 ChatGPT 登录。
- 本机 `codex app-server generate-json-schema`（0.149.0，无研究调用）确认 ThreadStartParams.ephemeral、TurnStartParams.outputSchema、threadId/turnId/tokenUsage 字段。生成协议文件仅用于文档核对，不复制平台代码。
- 无每维实际美元账单保证，无独立账号额度，无研究准确率或真实省钱结论。保留 session 不等于内存常驻，归档不等于立即卸载或永久保留。
