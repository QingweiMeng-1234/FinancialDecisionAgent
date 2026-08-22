# ADR-0003：Theme Chokepoint 受信验证与统一评估架构

## 状态

`ACCEPTED_FOR_IMPLEMENTATION`（2026-08-18）

本 ADR 是新的 Architecture Remediation Program v2 的实现决策，不是旧 Evidence Trust Review Program 的 Cycle 4，也不撤销 Cycle 3 的 `BLOCKED_REPEATED_ROOT_CAUSE`。它在实现层面取代 ADR-0002 中未能关闭根因的方案；ADR-0002、Remediation Program v1、RC-1 裁决和全部历史 receipt 保持只读。

## 决策依据

RC-1 最终独立裁决确认三项根因在 Cycle 1–3 连续存在：

1. `RC1-CANDIDATE-ROOT-0001`：调用方可控制 verification repository/ledger，并让该 ledger 充当自己的权威；
2. `RC1-CANDIDATE-ROOT-0002`：有限否定词和 polarity 规则代替会计语义核验；
3. `RC1-CANDIDATE-ROOT-0003`：Stage 6 没有调用覆盖全部必要输入和评分 family 的 Stage 3/4 canonical computation。

RC-1 还确认两项必须单独关闭的根因：

- `RC1-CANDIDATE-ROOT-0004P`：不同 wrapper、endpoint、token 字符串不能证明 provider-issued authenticated principal 隔离；
- monitoring evidence/assessment refresh atomicity：与 orchestrator manifest atomicity 属于同一 family，但不是同一根因，必须独立修复。

## 当前实现边界

当前实现已经具备 request/raw/result/reconciliation、SourceIdentity/SourceVersion、全局 provider trace、governance bundle 和部分 canonical recompute 结构，但这些结构仍不足以建立权威：

- `EvidenceBoundBusinessFactVerifier` 的 repository、raw client 和 `verifier_execution_id` 都由 composition caller 提供；字符串不相等只能证明标签不同，不能证明独立执行主体；
- `parse_business_fact_text_semantics` 通过 `unconfirmed/not/absent/planned` 等有限词法模式判定 polarity；Node validator 主要检查字段、哈希和 ledger 形状，不能证明 issuer/product/period/metric/value/basis 的会计含义成立；
- `Stage34CanonicalCalculator` 位于 Stage 6，并直接调用 Stage 3/4 私有函数；Company 路径只完整重算 Defensibility，Replacement、Earnings 和 Competition 缺少同等重算；Stage 3/4 初始计算也不通过该服务；
- production composition 只检查 boundary ID、endpoint 和 bearer token 字符串两两不同；`persisted_provider_identity` 是本地拼接值，不是 provider 或身份提供方签发的 principal；
- monitoring 先逐条保存 evidence，再执行 recompute，最后另行保存 refresh；中途失败后，已持久化 event 会被 duplicate guard 拒绝，无法以同一 operation 幂等收敛。

## 决策一：Capability 只能由受信 Verification Authority 授权

新增受控 `VerificationAuthority` 边界。调用方只能提交 assertion proposal 和不可变 source reference，不能选择 authority repository、写入 verification result、声明 verifier identity 或直接构造 capability receipt。

合法链条为：

```text
AssertionProposal
→ AuthorityVerificationRequest（I/O 前持久化）
→ ProviderRawResponse + PrincipalAttestation
→ StructuredFactCandidate
→ AuthoritySemanticDecision
→ AuthorityReconciliation
→ CapabilityDecisionReceipt
```

其中：

- authority trust anchor 固定在 Runtime Governance Bundle；
- production `PrincipalAttestation` 只能来自已验证的 mTLS peer identity，或由固定 issuer/JWKS 验证的 OIDC/JWT；caller label、header echo、token hash、对象 identity 和本地 UUID 均不是 attestation；
- controlled E2E 可使用单独标记的 fixture trust anchor，但 fixture receipt 永远不能进入 live/release evidence；
- `CapabilityDecisionReceipt` 必须绑定 authority principal、issuer、audience、key/certificate identity、request/raw/result/reconciliation ID、source version、assertion hash、semantic decision 和 governance bundle SHA；
- state consumer 必须按 receipt ID 从 authority-owned store 重新加载并核对，不能接受调用方提交的完整 receipt 对象；
- 缺失、过期、撤销、wrong audience、wrong principal、hash mismatch、repository mismatch 或 lineage 断裂一律不授权 capability。

## 决策二：会计事实使用结构化语义验证，不再扩展关键词黑名单

会计与 Earnings 事实的最小结构为：

```text
issuer_company_id
product_or_segment_id
document_identity + source_version_id
fiscal_period_id + fiscal_period_type
accounting_metric + accounting_basis
numeric_value + unit + currency
polarity + modality + lifecycle
exact_quote + quote_span
restatement_or_withdrawal_state
```

验证必须同时完成：

1. 原文 span 与 SourceVersion 原始字节/规范化正文一致；
2. 文档 publisher/issuer 与被评分公司一致；
3. product/segment attribution 在原文中明确成立，不能从公司整体收入自动外推；
4. period、period type、basis、metric、value、unit/currency 能形成同一条会计陈述；
5. affirmative/current/realized 由独立 semantic verifier 对结构化事实和原文共同裁决；
6. restatement、withdrawal、冲突版本和跨期间口径变化被显式处理；
7. quarter persistence 使用四个不同且连续、可比较的季度事实，年度、TTM、重复季度和混合 basis 不得替代。

词法规则只可用于 pre-screen、提示或显式负例发现，不能单独把事实变成 supported，也不能单独授权 `accounting_revenue_confirmed` 或 `multi_period_revenue_confirmed`。无法完整证明时保持 `unknown`；来源冲突时为 `conflicted`；0 分仍需明确失败、退出、未采用或不符合要求的证据。

## 决策三：Stage 3、Stage 4、Stage 6 共用唯一 Canonical Assessment Engine

从 Stage 模块中抽离版本化 `CanonicalAssessmentEngine`。Stage 3 初始 Segment 计算、Stage 4 初始 Company 计算和 Stage 6 monitoring 重算必须调用同一 engine identity、同一 governance bundle 和同一完整输入 contract。

Engine 输入为持久化并重新加载的 `AssessmentInputBundle`：

- Segment：全部 frozen dimensions、claims/cards/source versions、counter-route reconciliations、relief inputs 和 critic receipt；
- Company：Defensibility、Replacement、Earnings 全部 dimensions，capability receipts、challenger sets、Competition peer context、Segment lineage 和全部 evidence ownership；
- Monitoring：上述完整 baseline 加已验证的新 facts；不得只提交 changed family 或 after-state proposal。

Engine 每次输出完整 `CanonicalAssessmentResultBundle`，覆盖：

- Segment state、score interval、coverage、Hard Gates；
- Defensibility state/score；
- Replacement state/score 及 production/scale/realized 非补偿性 gates；
- Earnings state/score、Material Revenue 和 persistence；
- Competition state、challenger relation 和 cross-company context。

规则：

- Stage 3/4 不再拥有一套私有权威算法，Stage 6 也不得 import 私有评分函数拼装部分重算；
- monitoring evaluator 只能提出 changed facts/claims，不能提交 after-state、score、Gate 或 trend；
- 任一必需 family 输入缺失时对应 family fail closed/unknown，不能沿用旧分数冒充新计算；
- Competition 依赖 peer set，任何公司变化都必须对同一冻结 peer context 完整重算受影响集合；
- 只有 repository 在核对 engine execution receipt 后，才能以 compare-and-swap 更新 assessment head；
- 一条原子事实只有一个主要高档计分归属，`floor_only/context_only` 复用必须显式且不产生第二份主要高档 credit。

## 决策四：角色隔离由已认证 principal 证明

Company discovery、original evidence、scoring、critic、business-fact verifier、source resolver 和 canonical scorer 均需独立 `AuthenticatedPrincipal`。

生产环境必须满足：

- principal attestation 由 provider/IdP 签发并由本地固定 trust anchor 验证；
- role、principal subject、issuer、audience、credential key/cert identity 和有效期进入 request/raw/reconciliation lineage；
- 需要隔离的角色，其 principal subject 必须不同；仅 endpoint、session、wrapper、boundary ID 或 bearer token 字符串不同不满足要求；
- provider 无法提供可验证 principal 时，production composition fail closed；不得降级为本地自报 identity；
- controlled fixture principal 必须带 `environment=controlled_fixture`，并被 release validator 排除。

## 决策五：Monitoring 使用可恢复的 Durable Unit of Work

每次 refresh 使用稳定 `operation_id` 和 idempotency key，状态机为：

```text
PREPARED
→ EXTERNAL_IO_STARTED
→ RAW_RECORDED
→ VERIFIED
→ RECOMPUTED
→ COMMITTED

EXTERNAL_IO_STARTED → OUTCOME_UNKNOWN → RECONCILING
任何验证失败 → REJECTED_NO_STATE_CHANGE
```

约束：

- 外部 I/O 前持久化 request 和 operation；
- raw response、provider trace 和 reconciliation append-only；
- evidence usage、完整 assessment revision、assessment-head CAS、monitoring refresh 和 run status 在同一数据库事务中最终提交；
- 进程在最终提交前崩溃时，不得留下已消费 evidence 却没有对应 assessment/refresh 的可见状态；
- 相同 event/idempotency key 的重试必须加载原 operation 并 reconciliation，不能创建新 ID，也不能被 duplicate guard 永久阻塞；
- `OUTCOME_UNKNOWN` 先 reconciliation，再决定是否继续，不能盲目重发外部副作用调用；
- stale expected head 必须使最终提交失败，随后基于新 head 重新计算。

## 权威写入矩阵

| 组件 | 可写 | 不可写 |
|---|---|---|
| Assertion producer | proposal、source reference | verification、capability |
| Semantic verifier | structured candidate、semantic decision | score、state |
| Verification Authority | reconciliation、CapabilityDecisionReceipt | company/segment assessment |
| Monitoring evaluator | observation、changed fact proposal | after-state、score、Gate、trend |
| Canonical Assessment Engine | 完整 assessment result bundle | source provenance、provider identity |
| Repository transition service | 核对 receipt 后原子提交 revision/head/refresh | 外部事实内容 |
| Stage 3/4/6 | 调用服务、读取 persisted result | 自行计算或覆盖 canonical after-state |

## 统一 Fail-Closed 规则

以下任一条件成立时，不得授权 capability、Candidate、Realized、Earnings、Competition 或 monitoring transition：

- authority/principal attestation 缺失、无效、过期、撤销或 trust anchor 不匹配；
- request/raw/result/reconciliation/source/version 任一断链；
- 原文、issuer、product、period、metric、basis、value 或 scope 不一致；
- evidence 为 unknown/conflicted，且合同没有明确允许的 bound；
- governance bundle 或任一 child SHA 不匹配；
- assessment input family 不完整或不是 repository 重新加载结果；
- Stage caller 提交 after-state 或绕过 canonical engine；
- monitoring operation 未完成最终原子提交；
- fixture identity 被用于 live/release claim。

## 实施与迁移

实现采用 append-only schema migration，不覆盖历史 verification、assessment 或 monitoring 记录。旧记录默认不具有 v2 authority；只有通过明确的 re-verification/recompute 生成新 v2 receipt 后才能参与新状态迁移。

每个可独立验证的 invariant 必须遵循仓库 `AGENTS.md` 的 SELECT → TEST → RED → IMPLEMENT → GREEN → REGRESSION → SCOPE CHECK → RECORD EVIDENCE。没有观察到真实 RED 的测试只能作为回归证据。

## 非目标与证明边界

本 ADR 不修改 v1.4 权重、阈值、评分方向、primary_state 优先级、Golden/Holdout 答案或人工 Top-10 上限；不授权 live provider、额外成本、部署、发布或用户可见交付。

文档冻结只证明架构决策已被记录，不证明实现、迁移、E2E、旧 finding 关闭、排名质量、GO 或上线。
