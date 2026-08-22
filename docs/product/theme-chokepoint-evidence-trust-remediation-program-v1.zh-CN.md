# Theme Chokepoint Evidence Trust Remediation Program v1

## 1. 文档状态

```text
program_id: theme-chokepoint-evidence-trust-remediation-v1
short_name: TC-ETRP-v1
status: FROZEN_FOR_IMPLEMENTATION
authorized_on: 2026-08-17
predecessor_fix_loop_status: BLOCKED_REPEATED_ROOT_CAUSE
predecessor_cycle4_allowed: false
```

本项目是一次新的架构治理与 remediation program，不是旧 Fix Loop 的 Cycle 4。旧 Cycle 1–3 的 finding、RCA、receipt 和失败结论完整保留。

## 2. 产品批准回执

产品负责人已批准以下八项：

1. 事实验证权属于独立、持久化的 verifier request/raw/result/reconciliation，不能属于 assertion producer；
2. Runtime governance bundle 必须绑定 overlay、business-fact decision table、source identity schema/policy 的 exact SHA；
3. Counter evidence 必须在当前 run/route 实际生成并持久化 Candidate → Claim → EvidenceCard → SourceSnapshot/SourceVersion；
4. Provider trace 必须 repository 全局唯一并检测跨 run replay；
5. Company discovery、original evidence、scoring、critic 使用四个独立低层 client，并分别保存 request/raw/parsed/reconciliation；
6. Monitoring evaluator 只能提出新证据，只有 canonical scoring service 能写 after-state；
7. 建立 immutable SourceIdentity/SourceVersion，覆盖默认端口、尾斜杠、redirect、alias、reprint 和 unknown provenance；
8. 新项目不得冒充 v1.4 frozen contract，也不得修改历史 Golden/Holdout/freeze。

授权依据：当前会话中产品负责人明确回复“行，就这么做吧”。该回复批准本 program 的设计与实施，不构成 GO、上线批准或 benchmark 通过证明。

## 3. 目标

让 Theme Chokepoint Stage 1–7 的每个关键结论满足三个条件：

- **事实可验证**：结论能回到不可变原文、source identity/version 和独立 verifier execution；
- **执行可证明**：provider/model/canonical scorer 的 request/raw/parsed/reconciliation 分开持久化，不能靠 self-report；
- **状态权唯一**：只有中央 validator/canonical scoring service 能把已核对事实转成 capability、Gate、score 和 transition。

完成后，系统应能从 raw provider fixture 运行真实 production composition 的 controlled E2E，并在单独授权的 live-provider E2E 中证明外部调用路径可达。两者均不能单独证明排名有效或用户可见交付。

## 4. 只读起点

新项目从以下旧快照只读继承：

```text
git_head: 819fad0b84fe5d6d4c985a82b99cc291bfe72894
cycle3_snapshot_id: cycle3-98895839f36ca492e7cc7484ea8cc23d1fd0e78e0beee2daf64a047dd59b786a
cycle3_mandatory_file_count: 85
cycle3_inventory_sha256: 98895839f36ca492e7cc7484ea8cc23d1fd0e78e0beee2daf64a047dd59b786a
```

该快照只是“问题发生时的精确起点”，不是合格运行基线。新实现必须创建新的 program snapshot/manifest，不能覆盖 Cycle 3 输出。

## 5. 授权范围

允许修改：

- `src/event_collector/theme_chokepoint/` 中与五个 finding 直接相关的 contracts、repository、providers、Stage 3/4/6、composition root；
- `tests/test_theme_chokepoint_*.py` 中与新 invariant 直接相关的 RED/GREEN probes；
- `tools/theme-chokepoint/` 中新的版本化 schema、policy、bundle、validator 与公开反例测试；
- 新建的 `outputs/theme-chokepoint-evidence-trust-remediation-*`；
- 本 program 的 ADR、contract、manifest、receipt 和审计报告。

禁止修改：

- v1.4 权重、阈值、评分方向、primary_state 优先级和人类评分语义；
- 任何历史 Golden、Holdout、盲标、agreement、Top-10、adjudication 或 freeze 内容；
- Cycle 1–3 snapshot、inventory、developer evidence、Reviewer/Challenger/Exit 报告或 receipt；
- 为迎合实现而删除 probe、降低断言、改 fixture 答案或将 P0/P1 无机制证据地降为 P2；
- 与五个根因无关的顺手重构。

如果实现需要改变评分语义、历史答案或授权范围，状态变为 `BLOCKED_NEEDS_PRODUCT_DECISION`，必须停下请求产品决定。

## 6. 非目标

- 本 program 不重新设计 Theme Research PRD 的 Stage 1–7 产品流程；
- 不改变 `unknown != 0`、`evidence_state + bound_type`、区间与 Hard Gate 语义；
- 不将公司数量从人工 Top-10 复核扩大；
- 不以测试、HTTP 200、provider callback 或 controlled fixture 代替 deployed SHA 和用户可见结果；
- 不承诺本轮直接部署到生产。

## 7. 实施批次

以下为架构实施批次，不是 Fix Cycle。每批先出现能证明缺陷的 RED，再写生产代码。批次完成只代表该批 invariant 有实现证据，不代表旧 finding 已关闭。

### Batch A：Governance Bundle 与独立事实验证

覆盖：`C1-P0-002`、`C1-P1-004` 的共同根因。

交付物：

- versioned `BusinessFactAssertion` proposal contract；
- verifier request/raw/result/reconciliation contracts 和 repository；
- runtime governance bundle schema、builder、exact-SHA loader；
- 由同一 decision table 生成或调用的 Python/Node capability evaluator；
- official accounting fact 与 quarter lineage 的统一验证路径。

必须先观察的 RED：

- producer 提供 positive tag + `verified=true` + 任意 receipt 仍能授权；
- `production use remains unproven/was denied/is pending/is planned/ended` 仍能授权；
- assertion 正确但 verifier raw/result/reconciliation 缺失仍能授权；
- business-fact decision table 被替换但 runtime/validator 仍启动；
- `accounting revenue remains unconfirmed at 25 million` 携带完整 metadata 仍能授权；
- 四个重复季度或混合 fiscal basis 仍满足 persistence。

批次通过条件：正向 official fact 成功；negative、unknown、conflicted、planned、missing、wrong-scope、wrong-period、hash mismatch 全部 fail closed；Python/Node differential 为零。

### Batch B：Source Identity/Version 与全局 Execution Trace

覆盖：`C1-P1-001`、`C1-P1-005` 的 provenance/replay 根因。

交付物：

- immutable SourceIdentity/SourceVersion schema 和 policy；
- canonical URL/redirect/reprint/alias 解析器；
- provider execution trace repository 全局唯一约束；
- unknown/ambiguous provenance 不能建立 independent/new event 的 Gate。

必须先观察的 RED：

- `:80/:443` 默认端口、尾斜杠、fragment、tracking/query order 变体被当成新 source；
- known redirect 的起点与终点被当成两个行业事件；
- paraphrased reprint 无 provenance 时被当成独立新事件；
- 同一 upstream trace/raw response 换 run/query/route 后可再次接受；
- 本地随机 receipt 可冒充 provider receipt。

批次通过条件：所有 alias/replay 反例拒绝或保持 ambiguous/unknown；明确不同原始事件仍能通过；所有判断绑定 versioned policy SHA。

### Batch C：Counter Evidence 物化闭环

覆盖：`C1-P1-001`。

交付物：

- demand/supply/alternatives 三 route 的 request/raw/parsed/claim/card/source/reconciliation 完整链；
- `new_counter_evidence_ids` 外键和当前 run/route/window 约束；
- explicit negative 与 unknown coverage 的独立表示；
- Segment Candidate consumer 只读取 repository reconciliation。

必须先观察的 RED：

- 三个 well-shaped route object 没有 EvidenceCard 仍进入 Candidate；
- new ID 只存在字符串、不存在 Card/SourceVersion；
- route 只引用 pre-existing positive evidence；
- explicit negative 没有执行记录/result coverage；
- evidence 来自其他 run/window/provider；
- 一个 evidence item 在多个维度重复获得主要高档 credit。

批次通过条件：每个 declared new ID 都能从 Candidate 追到 raw provider bytes；任何断链都 withheld；允许的 `floor_only/context_only` 复用不会产生第二份主要高档 credit。

### Batch D：Company 四角色 Production Chain

覆盖：`C1-P1-002`，并为 `C1-P1-004` 提供 production assertion writer。

交付物：

- 四个两两独立 low-level client 与 factory identity check；
- non-test discovery/entity resolution/original-text extraction/assertion writer；
- DeepSeek ordinal parser + central validator；
- 独立 critic request/raw/parsed/reconciliation；
- public CLI/runtime composition。

必须先观察的 RED：

- 同一对象充当 discovery/evidence/scoring 三角色时 factory 仍接受；
- transport 直接返回 `CompanyScoringResult` 时仍接受；
- scorer 和 critic 共用一次 model response/trace；
- search summary 未抓原文就成为评分 evidence；
- alias 指向错误 company/product Scope；
- malformed DeepSeek output 绕过中央 validator。

批次通过条件：删除 test-local business implementation 后，controlled raw fixtures 仍能由 production code 生成所有 domain objects；四条执行链各自有持久化 receipt。

### Batch E：Canonical Monitoring Recompute 与真实 Controlled E2E

覆盖：`C1-P1-005`，同时验证 A–D 的组合。

交付物：

- canonical scoring service 的唯一 after-state 写权；
- monitoring observation → source/fact verification → canonical recompute → CAS transition；
- raw-boundary controlled Stage 1–7 E2E；
- failure/retry/OUTCOME_UNKNOWN reconciliation；
- 新 program 的 runtime conformance artifacts。

必须先观察的 RED：

- arbitrary evaluator JSON 可把 Gate 1 改成 Gate 2；
- knowledge revision time 可冒充新的 industry event time；
- missing/conflicted source 仍产生 strengthening；
- stale expected-before-state 仍覆盖较新 state；
- controlled E2E fixture 直接注入 completed domain object；
- 默认 runtime provider 使 required state 永远不可达。

批次通过条件：只有 canonical scorer 的 persisted/reloaded result 能改变 state；controlled E2E 从 raw fixture 穿过真实 company scoring chain；负向、unknown、conflicted、missing 和 bypass 路径都有产物与断言。

## 8. Mandatory Counterfactual Suite

新实现至少必须保留并扩展以下反例：

1. empty evidence 不能成为 supported；
2. missing counter-search 不能成为 Candidate；
3. Replacement 不能越过 production gates 直接到 Realized；
4. 非季度 period 不能满足 quarter persistence；
5. knowledge revision 不能伪装为新 industry event；
6. governance bundle 或任一 child SHA mismatch 必须 fail closed；
7. researcher self-reported GateResult 不能替代独立计算；
8. default runtime provider 不得让 required states 不可达；
9. 一条 evidence 只能有一个主要高档计分归属，`floor_only/context_only` 复用必须显式；
10. controlled E2E 必须穿过真实 company-scoring chain；
11. producer self-verification 不能授权 capability；
12. provider execution trace 不能跨 run replay；
13. counter ID 未物化为 Card/SourceVersion 时不能解锁 Candidate；
14. 四个 company client 不能由同一对象冒充；
15. arbitrary monitoring evaluator 不能写 after-state；
16. redirect/reprint/unknown provenance 不能证明新行业事件。

每个关键状态迁移必须含 positive、explicit negative、unknown、conflicted、missing-field、hash mismatch 和 Hard-Gate bypass。

## 9. 每批证据回执

每个 Batch receipt 必须记录：

- exact target snapshot ID、HEAD、git status 和 dirty file list；
- selected invariant；
- RED 命令、失败原因、测试输出 hash；
- production diff；
- GREEN 命令、输出 hash；
- relevant regression 和 broader regression；
- migration dry-run/apply/verify/rollback；
- writer/validator/reader/state consumer lineage；
- 未验证范围和 proof boundary。

没有真实观察到 RED 的测试只能称为 regression，不得称为 TDD 证据。

## 10. 新审核程序

全部实施批次完成后，才启动新的 `Initial Independent Review`。它不是旧 Cycle 4，cycle counter 从新审核程序的 0 开始，但必须把旧五个 root-cause fingerprint 全部放入公开 regression 和隐藏 holdout；不能通过改名清除 escape history。

审核角色隔离：

- Developer 只修复 adjudicated P0/P1；
- Reviewer A 关闭旧 finding，但无权签发最终 GO；
- 每轮 closure 后使用 fresh-context Challenger；
- 最终由 fresh-context Exit Auditor 推翻退出结论；
- 无法创建独立角色时，最高状态为 `READY_FOR_EXTERNAL_REVIEW`。

新审核最多五个 Fix Cycle；同一根因连续三轮复现仍立即 `BLOCKED_REPEATED_ROOT_CAUSE`，不能机械跑满五轮。

## 11. 退出门与状态

### 11.1 Implementation Complete

```text
all_batches_green == true
AND all_batch_receipts_complete == true
AND migration_verification_complete == true
AND controlled_e2e_actual_company_chain == true
```

只允许状态：`IMPLEMENTATION_COMPLETE_PENDING_INDEPENDENT_REVIEW`。

### 11.2 Technical Exit

```text
mandatory_file_coverage == 100%
AND mandatory_risk_coverage == 100%
AND mandatory_counterfactual_completion == 100%
AND open_P0 == 0
AND open_P1 == 0
AND false_closure_count == 0
AND challenger_new_valid_P0 == 0
AND challenger_new_valid_P1 == 0
AND exit_auditor_new_valid_P0 == 0
AND exit_auditor_new_valid_P1 == 0
AND exact_target_SHA_unchanged == true
AND exit_auditor_is_fresh_context == true
```

未运行隐藏 holdout 时，最高状态为 `TECHNICAL_GO_PENDING_HOLDOUT`。

### 11.3 Benchmark Exit

还必须满足：

- 外部隐藏 holdout 的 P0 recall = 100%；
- P1 recall 达到产品批准阈值；
- HBM/advanced-packaging v1.4 regression 通过；
- AI 数据中心电力/冷却 Top-10 重新复核；
- 每个主题人工复核不超过已批准的 10 家公司；
- 任何 post-exit escape 已登记并补充公开/隐藏 probes。

满足后才可由 fresh Exit Auditor 输出 `BENCHMARK_QUALIFIED_GO`。

### 11.4 Live Provider 与 Release

`BENCHMARK_QUALIFIED_GO` 不自动证明 live Tavily/DeepSeek、计费、部署或用户可见交付。上线前还需独立记录：

- exact deployed SHA；
- runtime governance bundle ID/SHA；
- Tavily/DeepSeek provider request/receipt；
- live Stage 1–7 terminal run state；
- artifacts 和 Top-10 结果；
- 用户可见交付 receipt。

该门通过后才可使用 `RELEASE_READY`；部署成功后仍需 `USER_VISIBLE_DELIVERY_VERIFIED` 才能宣称交付完成。

## 12. 停止条件

出现以下任一情况立即停止并报告：

- 需要修改评分语义或历史 Golden/Holdout/freeze；
- 同一根因在新审核连续三轮复现；
- target snapshot 在审计期间变化；
- mandatory coverage 不完整；
- finding 证据不足或尚未 adjudicate；
- 需要新增外部权限、密钥、成本或部署授权；
- fresh Reviewer/Challenger/Exit Auditor 无法独立运行。

最多五轮不能自动转化为 GO。结束状态只能是 `BLOCKED_*`、`READY_FOR_EXTERNAL_REVIEW`、`TECHNICAL_GO_PENDING_HOLDOUT`、`BENCHMARK_QUALIFIED_GO` 或经额外验证后的 `RELEASE_READY`。

## 13. 当前冻结边界

本合同冻结的是：问题范围、信任边界、允许修改范围、实施批次、RED probes 和退出门。

尚未证明的是：任何 Batch 已实现、测试已通过、controlled/live E2E 已跑通、旧五个 finding 已关闭、排名质量达标或产品已上线。
