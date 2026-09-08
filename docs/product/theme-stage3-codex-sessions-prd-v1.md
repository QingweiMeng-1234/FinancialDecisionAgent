# Stage 3 六维独立预算与 Codex Sessions PRD

## 文档控制

| 字段 | 值 |
| --- | --- |
| Status | READY_FOR_HUMAN_REVIEW |
| Version | 1.0-candidate.1 |
| Date | 2026-09-07 |
| Feature | theme-stage3-codex-sessions-v1 |
| Coding orchestration | 公共 Coding Agent Platform，LangGraph 单引擎 |
| Human freeze | 尚未批准；本文件不是 Developer-start 或 GO 授权 |

配套：[System Design / ADR / Migration](../architecture/theme-stage3-codex-sessions-system-design-v1.md)、[API / Data Contract](../api/theme-stage3-codex-sessions-api-v1.md)。

## 1. 目标与授权边界

将现有 Stage 3 的六个研究维度接入本机 ChatGPT 登录的 Codex 持久 session，实行独立用量预算、有界并发、可恢复的证据验收，并在完成后归档 session，减少活动列表杂乱。目标是执行与用量可控，不是证明多 Agent 研究质量优于单 Agent，亦不承诺比 API 便宜。

用户已确认：独立预算不互借；预算使用任务数、轮次、时间与 Token 软上限，而非精确美元；完成后归档而非删除；由公共平台的 Developer 实现，不由当前 Document Architect 写产品代码。用户此次只批准准备候选文档，未批准新冻结或 Coding run。

此特性是独立、默认关闭的候选研究入口，不是旧 single/isolated-workers 配对实验的升级实现。旧 Bundle `8189fa928769db5e9bd535e7e19a8b1ff77944df03357ee977cd0132aca06001` 及其文档、未批准的 v1.1 addenda 均保持原字节；本特性不继承其共享美元预算、配对评估或盲审承诺。以后若同时运行两种特性，按不同 execution namespace 隔离。

## 2. 六维语义及预算归属

| dimension_id（固定顺序） | 中文名称 |
| --- | --- |
| demand_pressure | 需求压力 |
| downstream_criticality | 下游关键性 |
| effective_supply_concentration | 有效供给集中度 |
| qualification_barrier | 资格认证壁垒 |
| capacity_inelasticity | 产能弹性不足 |
| substitute_weakness | 替代品薄弱程度 |

一个 execution_id 为一个已处于 SUPPLY_CHAIN_GRAPH_READY 的 base_run_id 创建六个 lane。**预算归属是 execution_id × dimension_id，覆盖该维度在本次运行的所有上游节点，不是每个节点重新获得六份预算。** 新 execution 创建新预算和新 session，不跨研究复用旧会话。

每个 lane 至多创建一个持久 session，按需惰性创建。零缺口且无需评分的 lane 不创建 session。同一 lane 内多个节点串行、按节点轮询；不同 lane 可并发，共享的只是 Codex turn 派发槽位，不是预算。max_concurrency 限制可能在途的 turn（含 dispatch intent 和终止未知 turn），不是所有 HTTP/清理操作总数；建议值 3，硬上限 6，启动请求显式提供。来源验证和生命周期操作另按 System Design §4 有界执行。

一个逻辑 task 是一个 node × dimension × round × kind 的有界调用工作单元；kind 为 research 或 assess。一个 attempt 对应至多一个 Codex turn；重试保留 task_id、产生新 attempt_id。模型内部的搜索/推理步数不是可严格计数的应用 task 数。

## 3. 范围

### 3.1 纳入

- 六 lane 的规划、轮询派发、独立预算账本、并发 admission；
- 本机官方 Codex App Server 的最小协议适配，使用既有 ChatGPT 登录，不提取/复制凭证；
- session 与 execution/lane 的持久绑定，原 session 内继续补查和维度评估；
- 原始来源独立获取、引用与作用域验证、稳定合并、候选评分；
- 失败隔离、迟到结果 fencing、重放幂等、明确的未知执行状态；
- 完成后的归档、归档查询核对与取消订阅；
- 离线协议测试、真实 Stage 3 集成测试和一次另行授权的本机 smoke 检查规程。

### 3.2 不纳入

- 不改 Stage 1/2/4/5/6/7 业务语义、冻结评分权重、评分阈值、证据规则或 canonical RunStatus；
- 不自动推广候选、不交易、不发布、不部署公共服务、不注册新的外部 MCP/REST 工具；
- 不添加产品内 LangGraph/Mastra 依赖，不复制公共平台 Prompt、运行器或历史 task 目录；本次 LangGraph 选择仅指 Coding run 的编排权；
- 不改变公共平台 Developer/Reviewer/Challenger 的模型、临时 session 策略或写入权；
- 不声称六个研究 session 具有独立账号额度、独立反方评审或硬美元成本；
- 不购买 credits、不兑换 reset、不自动切换 API key、不调用新的付费检索服务；
- 不删除、rollback、显式 compact 用户 session，不归档其他任务，不创建子 Agent。

## 4. 用量行为

REQ-01：六个 lane 的 limit 必须在创建前明确并冻结。每个包含 max_tasks、max_turns、max_rounds、max_elapsed_seconds、turn_timeout_seconds、token_soft_limit、max_attempts_per_task、max_source_documents。没有生产默认额度；测试数值仅用于 fixture。

REQ-02：max_tasks 计独立已 admission 的逻辑 task；max_turns 计所有可能已发送的 turn attempt，包括重试和无法确定是否发送的 attempt。任务/turn 额度在发送前原子扣占；不因空结果、超时、schema 错误或重启退回。只有明确未尝试发送且事务未提交，才没有消耗。SDK 隐式 turn 重试必须关闭。

REQ-03：max_rounds 是本 lane 接纳的最大 round 编号；max_elapsed_seconds 从 lane 第一次 admission 的持久 UTC 时间起计，包括排队、断线、账号等待和重启。耗尽后不恢复研究；新预算必须新建 execution。turn 截止取 lane 截止与 turn_timeout 中较早者。超时要求 interrupt，不能声称远端计费或执行已回滚。

REQ-04：Token 以服务器 thread 用量事件为观测值，不把 cached/reasoning 分项重复相加；达到软上限后禁止新 turn，并请求中断在途 turn。超出软上限的实际观测仍如实记录。缺失或不可信 telemetry 使该 lane 暂停，不记为 0；账号整体限流会暂停所有 lane，独立预算不绕过账号限制。

REQ-05：各 lane 不共享、转移或自动补充额度。sum(max_source_documents) 不得超过 base ResearchRequest.max_sources；每 lane 的 max_rounds/max_elapsed_seconds 不得超过 base request 对应上限。这是启动时静态边界校验，不是运行时共享成本池。request.max_cost_usd 不适用于此 subscription 模式；实际美元费用标记为 null/unavailable。

## 5. 研究与输出

REQ-06：初始 task 根据所有 depth>0 的节点规划，维度顺序固定。每个非空 lane 先获得第一次 admission，再进入下一轮；槽位释放即派发，不等待慢 lane 才保存快 lane 的证据。每个 lane 一次最多一个 turn。

REQ-07：research turn 只提交证据候选；模型提供的 URL、原文、日期均是不可信输入。协调器必须按来源策略独立获取原文，保存来源快照，验证 exact_quote、日期、node/维度/scope 和 claim lineage，才能接纳。搜索摘要不能充当 original_text；验证不证明来源本身真实。

REQ-08：assess turn 使用已接纳的本节点、本维度 evidence IDs，输出现有 v1.4 维度提案 schema。提案不是权威分数；现有验证、跨维证据所有权检查、权重和门槛计算仍由代码执行。每个 assess turn 也扣本 lane 的任务/turn/Token 额度，不可另调无预算的 DeepSeek 或第七个 session。新提案失败时仅在当前 evidence basis 未变且重验通过的情况下保留旧有效提案，否则使用现有 unknown 维度构造；不伪造确定分数。适用性键见 API §3.3。

REQ-09：首期只适配当前 Stage 3 的 v1.4 执行合同；不自动升级为 v1.6/v1.6.1，不绕过 FrozenScoringContract/governance 校验。旧 critic/relief 暂不接入新路径，缺少的 gates 仍按现有代码保留；本次不宣称盲审完成。最终必须产生经过原 validator 的 candidate_result_sha256 或明确的 finalization_failed，而不是用“六个 Agent 都回答了”作为完成标准。

REQ-10：candidate evidence、assessments、操作回执只写新特性的执行数据库，不写原 run、原证据账本或 signals。相同快照、相同验证后输出的合并顺序不受完成先后影响；不同 LLM 调用不保证逐字复现。

## 6. Session 完成与轻量化

REQ-11：只创建全新非 ephemeral 研究 session，不 fork 当前对话、不复用用户已有 session。对应关系和 ownership intent 落盘；名称使用 `Theme S3 | run-short | dimension`，完整身份依赖持久映射而非标题。临时安全工作目录按 execution/lane 隔离，不修改全局 CODEX_HOME、登录状态或其他服务。

REQ-12：lane 终止前先保存已验收证据、最终维度提案或失败原因、用量和 turn 回执；确认没有活动 turn 后才归档。归档后通过同一 Codex 服务核对 archived 状态，再取消本连接订阅。协议的闲置卸载有等待期，不承诺立即释放内存；不主动压缩上下文。

REQ-13：归档/取消订阅失败不删除研究结果、不重跑模型；持久化 cleanup_pending/cleanup_failed，允许仅重试清理。所有可确认归属的终止 lane 都需清理，包括预算耗尽与研究失败。发送/创建结果不确定的 lane 先保留待核对，禁止无证据地归档可能正在运行的会话。

REQ-14：默认列表不再显示本 execution 的已归档 session，并能通过保存的 thread_id 读取/恢复查看历史。归档不是永久保留保证，仍受账号 retention 约束；产品证据快照必须独立保存。自动归档不得处理当前主任务、其他用户 session 或公共 Coding 角色。

## 7. 验收矩阵

| ID | 验收场景 | 关联 |
| --- | --- | --- |
| AC-01 | 六维 limit 独立；A 用尽不扣减或停止 B；重启不重置额度 | REQ-01..05 |
| AC-02 | 多节点只有最多六个 session；可能在途 turn 数始终不超过配置，同 session 不重叠 turn，来源/生命周期并发也满足各自界限 | REQ-06,11 |
| AC-03 | A 卡住时 B/C 结果立即可从账本读取；终止确认前 A 仍占槽 | REQ-06,12 |
| AC-04 | duplicate usage 不重复计数，达到 Token soft limit 停止后续 turn，未知 usage 不作零成本 | REQ-04 |
| AC-05 | turn、任务、round、时间及来源上限分别有边界测试；账号限制不自动购买或换认证 | REQ-01..05 |
| AC-06 | research/assess/重试全部归属本 lane；没有未记账的 scorer/critic/API 调用 | REQ-02,08,09 |
| AC-07 | 假原文、错对象、未来日期、未知 evidence ID 被拒；失败不变成“查无证据” | REQ-07..09 |
| AC-08 | 接纳结果、task 状态、usage 与 receipt 原子可见；崩溃恢复不重复已完成 task | REQ-10 |
| AC-09 | 丢失 thread/start 或 turn/start 回应时停止盲重发；迟到旧 attempt 不覆盖新 owner | REQ-11..13 |
| AC-10 | 成功、失败、耗尽均先落盘后归档；归档失败只重试清理，其他 session 零变化 | REQ-12..14 |
| AC-11 | 候选评分复用现有验证规则，原 canonical Stage 3 与 RunStatus 字节/语义不变 | REQ-08..10 |
| AC-12 | 新数据库初始化、未知 schema 零写入拒绝、旧 RPC/领域库零迁移、禁用保留数据 | System Design §8 |
| AC-13 | 协议能力或 ChatGPT 登录不满足时前置失败；离线测试无真实 session/网络付费调用 | System Design §3 |
| AC-14 | 产品研究 session 与公共 Coding 临时角色隔离；无框架/Prompt复制、无自动 GO | §1,3 |

AC-01..13 必须有严格 TDD 的 RED/GREEN 与相关回归。真实供应商取消、实际额度消耗、桌面列表行为只能由另外明确授权的小额度 smoke 验证；离线成功不等于部署或省钱证明。Coding Human Final Confirmation 必须报告 exact Frozen Bundle SHA、candidate snapshot SHA 和开放 P2，不得代替用户 GO。
