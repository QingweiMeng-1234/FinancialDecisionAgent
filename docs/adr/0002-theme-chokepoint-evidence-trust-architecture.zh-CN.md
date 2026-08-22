# ADR-0002：Theme Chokepoint Evidence Trust Architecture

## 状态

Accepted for implementation（2026-08-17）。

本 ADR 只批准新的证据信任架构和实施边界，不批准上线、不宣称真实 E2E 已通过，也不修改或重新冻结 v1.4 评分语义。

## 决策所有者

- Financial Agent 产品负责人
- Theme Chokepoint 架构负责人
- Evidence/Scoring Runtime 实现负责人
- 独立审计负责人

## 背景

旧 Fix Loop 在 Cycle 3 以 `BLOCKED_REPEATED_ROOT_CAUSE` 终止。Cycle 3 的 85 文件目标快照为：

```text
cycle3-98895839f36ca492e7cc7484ea8cc23d1fd0e78e0beee2daf64a047dd59b786a
git HEAD: 819fad0b84fe5d6d4c985a82b99cc291bfe72894
```

五个根因连续三轮复现：

1. `C1-P0-002`：事实 assertion 及 verification 字段可由同一 producer 自证，decision table 未绑定 expected SHA；
2. `C1-P1-001`：counter evidence ID 未物化为当前 route 的 Claim/Card/SourceSnapshot，provider trace 可跨 run replay；
3. `C1-P1-002`：company discovery、evidence、scoring、critic 未形成四个独立低层执行边界；
4. `C1-P1-004`：完整但自报的 accounting metadata 仍可授权明确写着“unconfirmed”的收入事实；
5. `C1-P1-005`：任意 monitoring evaluator 仍可写 after-state，source identity/version 和 redirect/reprint provenance 不完整。

这些问题不是再补几条 blacklist 或 fixture 就能解决，而是“谁有权声明事实、谁有权改变状态、什么记录能证明执行发生”没有形成统一边界。因此不启动 Cycle 4，而是建立新的 `Theme Chokepoint Evidence Trust Remediation Program v1`。

## 核心决策

### 1. Producer 只能提出事实，不能验证自己

`BusinessFactAssertion` 是待验证命题，不是评分授权。Assertion producer 可以写：

- subject、product、predicate 候选；
- 原文 span、source version 候选；
- polarity、modality、lifecycle、metric/value/unit/period 候选。

Producer 不得写入或伪造可授权评分的 `verified=true`、verifier receipt、verification status 或 capability。任何 caller 传入的这些字段都必须被忽略或拒绝。

评分授权只来自以下持久化链条：

```text
BusinessFactVerificationRequest（I/O 前）
→ BusinessFactVerificationRawResponse（验证边界内）
→ BusinessFactVerificationResult（中央 parser/validator）
→ BusinessFactVerificationReconciliation（repository 重载并核对）
→ CapabilityDecision（只读治理 bundle + 已核对事实）
```

四类记录使用不同 ID、不同写入时点和内容哈希。State consumer 必须按 ID 从 repository 重载，不能信任调用者提交的完整结果对象。

### 2. Runtime governance bundle 必须精确绑定所有语义控制

每次运行在开始时加载一个不可变 `RuntimeGovernanceBundle`，至少绑定：

- runtime overlay 的路径、contract ID、SHA-256；
- business-fact decision table 的路径、schema version、SHA-256；
- source identity schema 的路径、schema version、SHA-256；
- source identity policy 的路径、policy version、SHA-256；
- canonical scoring contract/implementation identity；
- bundle 自身的 ID 和 SHA-256。

运行请求必须提交 expected bundle ID/SHA。缺失、文件不匹配、运行中变化、decision table 漂移或 source policy 漂移一律 fail closed。仅传 `--allow-unfrozen-overlay` 不能绕过 bundle 校验。

现有 v1.4 human scoring contract 保持历史冻结语义；现有 v1.4.1 runtime overlay 仍标记为 unfrozen。新 bundle 是该 remediation program 的执行控制，不得冒充历史 freeze manifest 所封存但当前不可得的 machine artifact。

### 3. Counter search 必须真正产生当前 route 的新证据对象

Segment Candidate 所需的 demand、supply、alternatives 三条 counter route，每条都必须形成：

```text
CounterSearchRequest
→ ProviderRawResponse
→ ParsedCounterFinding
→ CandidateClaim
→ EvidenceCard
→ SourceSnapshot / SourceVersion
→ CounterSearchReconciliation
```

`new_counter_evidence_ids` 只是上述已持久化 EvidenceCard 的外键集合，不能是自由字符串。每个 ID 必须：

- 属于当前 run、segment、route 和 retrieval window；
- 能回溯到本次 raw response；
- 通过 SourceIdentity/SourceVersion 去重；
- 被 route semantics 实际消费；
- 与正向主证据区分主要高档计分归属，同时允许合同定义的 `floor_only/context_only` 复用。

没有具名新证据时，可以记录 executed explicit negative 或 unknown coverage，但不能伪造“发现了新 counter evidence”。

### 4. Provider trace 在 repository 全局唯一

Provider trace 唯一性不局限于一个函数或 run。Repository 对以下稳定身份建立全局唯一约束：

```text
provider_identity
provider_account_or_route_identity
upstream_trace_id / receipt_id（若上游提供）
raw_response_sha256
```

同一 provider execution 被换 run、query、route、时间戳或本地 ID 重放时必须拒绝。上游没有 trace ID 时，使用 policy 定义的替代 identity，并显式标记证明强度；不能把本地随机 ID 当作 provider receipt。

### 5. Company chain 使用四个独立低层 client

Production composition root 必须构造四个两两独立的 client：

1. company discovery client；
2. original-text evidence client；
3. company scoring model client；
4. independent critic model client。

四者不能是同一对象的不同方法，也不能共享一个可直接返回最终 domain batch 的 facade。每个 client 只返回低层 raw response，并分别持久化 request/raw/parsed/reconciliation records。若底层网络 transport 必须共享，必须有四套独立的 authenticated invocation boundary、role ID、request ID、trace 和持久化表，并由专门 ADR 证明等价隔离；v1 默认不允许此例外。

非测试代码负责：entity/scope normalization、原文抽取、assertion 生成、ordinal parsing、中央验证和 critic 解析。Controlled E2E 只能 fixture 外部 raw bytes/response，不能 fixture `CompanyScoringResult` 等最终业务对象。

### 6. Accounting/Earnings 复用同一个独立事实验证边界

不建立 Earnings 专用的弱验证器。`accounting_revenue_confirmed` 只能由经过独立核对的官方会计事实派生。至少要求：

- issuer/company Scope 与 product/segment attribution；
- official document 的 SourceIdentity/SourceVersion；
- affirmative polarity，非 planned/target/expected/unconfirmed；
- accounting metric、numeric value、unit/currency；
- fiscal period、period type、accounting basis；
- restatement/withdrawal/conflict 状态；
- verifier request/raw/result/reconciliation lineage。

四季度 persistence 必须是四个不同、可比季度的记录。重复季度、混合年度/季度、口径变化、缺单位、只证明订单/出货/产能、无法归因到被评分产品时均 fail closed 或保持 unknown。

### 7. 只有 canonical scoring service 能写 after-state

Monitoring evaluator 只负责提出新 source/fact candidate，不能直接提交 after score、mandatory gate level、trend 或 state transition。

合法链条为：

```text
MonitoringObservation
→ Source/Fact verification
→ CanonicalScoringRequest（I/O 前持久化）
→ CanonicalScoringRawResponse / execution record
→ CanonicalScoringResult（中央 validator）
→ CanonicalScoringReconciliation（repository 重载）
→ MonitoringTransition
```

Canonical scoring service 必须使用与 Stage 3/4 相同的治理 bundle 和状态计算器。Monitoring 只能消费其持久化结果，不能信任 evaluator JSON 中的 after-state。

### 8. SourceIdentity 与 SourceVersion 不可变且分层

`SourceIdentity` 表示逻辑文档/发布物/原始事件；`SourceVersion` 表示该 identity 的一次可核验版本。最低字段包括：

`SourceIdentity`

- canonical publisher ID；
- canonical document/event ID（若可得）；
- canonical URL key；
- redirect terminal identity 与 redirect chain；
- original/reprint/alias relation；
- provenance state：verified、ambiguous、unknown。

`SourceVersion`

- retrieval time；
- raw bytes SHA-256；
- normalized content SHA-256；
- exact quote/span identity；
- content type、language、publication/update time；
- parent identity 和 version sequence。

Canonical URL policy 至少处理：scheme/host case、IDN、默认端口、尾斜杠、fragment、tracking parameter、query ordering、percent encoding、known redirect。URL 不同不证明事件不同；正文 hash 不同也不自动证明行业新事件。无法判断 paraphrased reprint 的 origin 时为 ambiguous/unknown，不能建立 `industry_event`。

## 权限矩阵

| 组件 | 可以写什么 | 不能写什么 |
|---|---|---|
| Assertion producer | assertion proposal、source span | verified capability、GateResult |
| Independent verifier | request/raw/result | score、company state |
| Repository reconciler | reconciliation receipt | 外部事实内容 |
| Counter parser | finding/claim/card candidate | Candidate state |
| Company scorer | proposed ordinal output | canonical accepted score、critic receipt |
| Independent critic | critique/admissibility proposal | 原始 score 或 after-state |
| Canonical scoring service | validated score/state result | source provenance |
| Monitoring evaluator | observation/fact candidate | after-state、trend |
| State-transition consumer | 使用已核对 receipt 转移状态 | 根据 caller self-report 转移 |

## 统一 fail-closed 规则

以下任一条件成立时，不得授权 capability、Hard Gate、Candidate、Realized、Earnings 或 Monitoring transition：

- request/raw/result/reconciliation 任一缺失；
- hash、run、route、scope、period 或治理 bundle 不一致；
- verifier 与 producer 是同一 execution identity；
- provider trace 重放或 identity 冲突；
- evidence ID 未物化或未被实际语义消费；
- polarity 为 negative/uncertain/planned/historical/withdrawn；
- evidence state 为 unknown/conflicted 且合同没有明确允许 lower/upper bound；
- source provenance 为 ambiguous/unknown 且该状态要求“独立新事件”；
- scorer/critic client 未隔离；
- after-state 不是 canonical scoring service 持久化并重载的结果。

缺少公开证据仍然是 `unknown`，不是 0 分。0 分仍需明确失败、退出、未采用或不符合要求的证据。本 ADR 不改变 v1.4 的 `evidence_state + bound_type`、区间、Gate 或评分方向。

## 数据完整性与幂等性

- 所有 request 在外部 I/O 前落库；
- raw response 只追加，不覆盖；
- parsed result 绑定 raw hash 与 parser version；
- reconciliation 绑定当前 repository 读取到的四段 lineage；
- retry 使用同一 operation ID，状态为 `OUTCOME_UNKNOWN` 时先 reconciliation，不以新 ID 重试；
- canonical state transition 采用 compare-and-swap 或等价 expected-before-state 约束；
- 任何修订都创建新事实/SourceVersion/assessment revision，不改写历史记录；
- knowledge revision time 不能伪装成新的 industry event time。

## 实施映射

| Finding | 架构修复 |
|---|---|
| C1-P0-002 | 决策 1、2、6 |
| C1-P1-001 | 决策 3、4、8 |
| C1-P1-002 | 决策 5 |
| C1-P1-004 | 决策 1、2、6 |
| C1-P1-005 | 决策 2、7、8 |

## 不在本 ADR 范围内

- 修改 v1.4 权重、阈值、primary_state 优先级或评分方向；
- 修改历史 Golden、Holdout、Top-10、盲标结果或 freeze manifest；
- 以 controlled E2E 证明 live provider、计费、部署或用户可见结果；
- 以新 program 名称清除旧 finding 历史；
- 直接宣布 GO。

## 被拒绝的替代方案

### 继续添加关键词 blacklist

拒绝。它无法穷举“unconfirmed、denied、pending、targeted”等表达，也没有改变 producer 自证的权力结构。

### 只给 self-reported object 增加更多 receipt 字段

拒绝。对象形状和本地 hash 只能证明局部一致，不能证明外部执行或独立验证发生。

### 继续进入 Cycle 4

拒绝。Cycle 3 已触发同根因连续三轮复现的强制终止条件；继续编号会违反已接受的退出协议，也会掩盖问题已经从局部 bug 升级为架构信任边界缺失。

### 修改 Golden/Holdout 让当前实现通过

拒绝。Benchmark 是独立约束，不是实现可修改的答案。

## 后果

正面后果：评分和状态变化可回溯到真实执行边界；counter search、公司评分和 monitoring 不再依赖自报对象；同一治理 bundle 约束 runtime、validator 和 monitoring。

成本：需要新增持久化表、迁移、全局唯一约束、四角色 client、source identity/version、canonical scoring service 以及更多负向测试；E2E fixture 必须下沉到 raw provider boundary。

## 验证要求

每个独立 invariant 必须按仓库 `AGENTS.md` 完成 SELECT INVARIANT → RED → IMPLEMENT → GREEN → REGRESSION → SCOPE CHECK → RECORD EVIDENCE。最终退出还必须满足新 program contract 中的独立 Reviewer、fresh Challenger、fresh Exit Auditor、hidden holdout、HBM regression 和 Top-10 复核门槛。测试全绿本身不等于 GO。

## Supersession

后续 ADR 可以替换实现技术，但必须逐项说明如何保留或替换本 ADR 的八项核心决策。任何弱化 verifier 独立性、全局 replay 防护、四角色隔离、source provenance 或 canonical scorer 唯一写权的变更，必须由产品负责人重新批准，并建立新的基线与退出门。
