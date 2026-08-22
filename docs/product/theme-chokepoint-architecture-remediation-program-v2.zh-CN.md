# Theme Chokepoint Architecture Remediation Program v2

## 1. Program 状态

```text
program_id: theme-chokepoint-architecture-remediation-v2
short_name: TC-ARP-v2
status: FROZEN_FOR_IMPLEMENTATION
authorized_on: 2026-08-18
predecessor_review_status: BLOCKED_REPEATED_ROOT_CAUSE
predecessor_cycle4_allowed: false
```

本 Program 是 RC-1 裁决后的新架构实施项目，不是旧 Review Program 的 Cycle 4，也不是 Remediation Program v1 的续编号修补。旧 Program、Cycle 1–3、root history、finding 和 receipt 全部保持只读；新名称不能清除任何 escape history。

## 2. 目标与成功边界

目标是关闭三个连续三轮复现的根因，以及两个必须独立关闭的现存根因：

| Workstream | 根因 | 目标控制 |
|---|---|---|
| W1 | RC1-CANDIDATE-ROOT-0001 | caller 无法选择、伪造或回填 Verification Authority |
| W2 | RC1-CANDIDATE-ROOT-0002 | 原文与结构化会计语义共同授权，不再由关键词授权 |
| W3 | RC1-CANDIDATE-ROOT-0003 | Stage 3/4/6 调用同一完整 canonical engine |
| W4 | RC1-CANDIDATE-ROOT-0004P | 角色隔离由 provider-issued authenticated principal 证明 |
| W5 | monitoring refresh atomicity | evidence、assessment head、refresh 和 run state 可恢复地原子提交 |

实现完成的最高状态为：

```text
IMPLEMENTATION_COMPLETE_PENDING_INDEPENDENT_REVIEW
```

测试、validator `valid=true`、controlled E2E、HTTP 2xx 或本 Program 的 freeze receipt 均不能签发 GO。

## 3. 不可变产品合同

本 Program 不得改变：

- v1.4 权重、阈值、评分方向、primary_state 优先级和非补偿性 Gate；
- `unknown != 0`；0 分必须有明确失败、退出、未采用或不符合要求的证据；
- `evidence_state + bound_type`、区间和 `bound_basis`；
- 一条原子事实只有一个主要高档计分归属，允许显式 `floor_only/context_only` 复用；
- 人工 review candidate 每个主题最多 10 家公司；
- 历史 Golden、Holdout、盲标、agreement、adjudication、Top-10 和 freeze bytes。

实现若需要改变上述任一项，立即转为 `BLOCKED_NEEDS_PRODUCT_DECISION`。

## 4. 冻结起点

v2 从单独的 67 文件 start inventory 开始，包含 runtime、tests、machine controls 和只读 predecessor evidence。精确 inventory、HEAD、工作树状态与 SHA 绑定在：

- `reports/theme-chokepoint-architecture-remediation-v2/target-inventory-v2.tsv`
- `reports/theme-chokepoint-architecture-remediation-v2/start-snapshot-v2.json`
- `reports/theme-chokepoint-architecture-remediation-v2/program-manifest-v2.json`
- `reports/theme-chokepoint-architecture-remediation-v2/program-freeze-receipt-v2.json`

当前工作树包含大量预先存在的未跟踪/脏文件；全部视为用户资产。每批只允许修改自己在 batch receipt 中声明的文件，禁止 reset、checkout、清理或覆盖无关内容。

## 5. 授权修改范围

允许在严格 TDD 下修改：

- `src/event_collector/theme_chokepoint/` 与 public Theme Chokepoint CLI/composition；
- `tests/test_theme_chokepoint_*.py` 中非 predecessor-validator 的相关 probes；
- `tools/theme-chokepoint/` 中 v2 machine controls、schemas、governance bundle、semantic validator 和 migration verifier；
- 新建 append-only migration、v2 runtime conformance、batch receipt 与 review artifacts；
- ADR-0003 和本 Program 的 append-only 勘误文件。

禁止修改：

- RC-1 contract、最终 root adjudication、Cycle 3 finalization、v1 manifest/freeze receipt 和 validator v1.2；
- 历史 Golden/Holdout/freeze；
- 为通过测试而删除 probe、降低断言、改 benchmark 答案、跳过 production path 或把缺证据改成 0；
- 与 W1–W5 无关的顺手重构。

## 6. 实施顺序

### Batch 0：v2 Contract、Schema 与 Migration Skeleton

交付物：

- `AuthorityPrincipalAttestation`、`AuthorityVerification*`、`StructuredAccountingFact`、`CapabilityDecisionReceipt`；
- `AssessmentInputBundle`、`CanonicalAssessmentResultBundle`、`MonitoringRefreshOperation`；
- append-only schema migration、dry-run/apply/verify/rollback contract；
- v2 Runtime Governance Bundle schema，绑定 trust anchors、semantic authority 和 canonical engine identity。

先观察 RED：

- 旧 schema 能接受 caller-authored authority receipt；
- 旧 assessment result 缺少 full-family input digest 仍可写 head；
- monitoring 没有 operation/idempotency state 仍可写 evidence；
- governance bundle 不绑定 principal trust anchor 或 engine identity 仍能启动。

通过条件：新 schema 能表达全部必需字段；旧记录默认无 v2 authority；migration dry-run、apply、verify、rollback 均有机械证据。

### Batch 1：Trusted Verification Authority 与 Accounting Semantic Authority

覆盖：W1、W2。

交付物：

- composition-owned Verification Authority；
- provider/IdP principal attestation verifier；
- request/raw/structured-candidate/semantic-decision/reconciliation/capability 完整持久化；
- 原文 span、issuer/product/period/metric/basis/value 的结构化会计验证；
- Python runtime 与 Node artifact validator 对同一 authority receipt 的一致消费。

必须观察 RED：

- caller 注入 repository row 或自报 verifier ID 后获得 capability；
- repository 替换、跨 run receipt、跨 source version receipt 可重放；
- unsigned/expired/wrong-audience/echoed principal 被接受；
- `revenue remains unconfirmed`、`targeted revenue`、`no reported revenue`、`production use ended` 因携带完整字段而获授权；
- 公司总收入被错误归因到产品/segment；
- 重复季度、年度、TTM 或混合 accounting basis 满足四季度 persistence；
- negative、unknown、conflicted 能授权 Earnings。

通过条件：只有 authority-owned store 中、带有效 principal attestation、原文和结构化语义均成立的 reconciled fact 能授权；negative/unknown/conflicted/planned/missing/wrong-scope/wrong-period/hash mismatch 全部 fail closed。

### Batch 2：Unified Canonical Assessment Engine

覆盖：W3。

交付物：

- 独立于 Stage 文件的版本化 `CanonicalAssessmentEngine`；
- Stage 3、Stage 4、Stage 6 使用同一入口、同一 bundle 和同一完整 input schema；
- Segment、Defensibility、Replacement、Earnings、Competition 全 family 计算；
- repository 重新加载、input digest、engine execution receipt 和 assessment-head CAS。

必须观察 RED：

- Stage 6 只重算 Defensibility，Replacement/Earnings/Competition 沿用旧值；
- Replacement 可越过 production/scale/realized Gate；
- Earnings 在缺会计 capability 时仍升档；
- Competition 在 challenger set 变化后不重算 peer set；
- Stage 3/4 与 Stage 6 对相同 input bundle 输出不同；
- arbitrary evaluator after-state、partial family JSON 或 missing family input 可写 assessment head；
- 一条 evidence 在多个 family 获得重复主要高档 credit。

通过条件：三条 Stage 路径对相同完整 input byte-for-byte 得到同一 authority result；缺输入保持 unknown/withheld；所有非补偿性 Gate、evidence ownership 和 state priority 由同一 engine 执行。

### Batch 3：Authenticated Provider Principal Isolation

覆盖：W4。

交付物：

- discovery/evidence/scoring/critic/fact-verifier/source-resolver/canonical-scorer 的 principal attestation；
- role-to-principal policy、issuer/JWKS 或 mTLS trust-anchor binding；
- request/raw/reconciliation 中的 principal lineage；
- controlled fixture 与 production principal 的不可混用标记。

必须观察 RED：

- 四个不同 token 字符串映射到同一 principal 仍通过；
- caller 修改 boundary ID/header 就能伪造隔离；
- 相同 principal 经不同 wrapper/session/endpoint 充当多个角色；
- principal attestation 缺失、过期、撤销或 wrong audience 仍可执行；
- fixture principal 被 release validator 接受。

通过条件：需要隔离的角色由 provider/IdP 验证为不同 principal subject；无法证明时 production fail closed。

### Batch 4：Monitoring Durable Unit of Work

覆盖：W5。

交付物：

- stable operation ID/idempotency key 与 durable state machine；
- I/O-before/after reconciliation、`OUTCOME_UNKNOWN` 恢复；
- evidence usage、assessment revision/head CAS、refresh、run status 的最终单事务提交；
- crash/fault injection 和重复请求恢复 probes。

必须观察 RED：

- evidence 保存后、recompute 前崩溃留下半提交；
- 同 event 重试被 duplicate guard 永久阻塞；
- `OUTCOME_UNKNOWN` 用新 ID 重发外部调用；
- stale head 覆盖较新 assessment；
- refresh 已保存但 run status/head 未同步，或反向发生；
- knowledge revision 冒充新的 industry event。

通过条件：任意 fault point 后系统要么无可见状态变化，要么能以同一 operation reconciliation 到唯一提交；不存在 consumed-evidence-without-assessment 或 assessment-without-refresh。

### Batch 5：Production Composition、Controlled E2E 与兼容回归

交付物：

- production composition 默认连接 v2 authority、engine、principal verifier 和 monitoring UoW；
- raw-boundary controlled Stage 1–7 E2E，不得直接注入 completed domain object；
- positive、explicit negative、unknown、conflicted、missing-field、hash mismatch 和 Hard-Gate bypass 全路径 artifacts；
- HBM/advanced-packaging 与 AI 数据中心电力/冷却回归输入保持历史只读。

必须观察 RED：

- 默认 runtime 使用 NoEvents/legacy path 使必需状态或 v2 controls 不可达；
- fixture 绕过 company discovery/evidence/scoring/critic 或 authority；
- controlled fixture receipt 被标成 live provider evidence；
- v1 historical artifacts 被重写才能通过。

通过条件：controlled E2E 从 raw provider boundary 穿过实际 production composition、完整 company scoring chain、v2 authority 和 monitoring commit；所有历史 sentinel SHA 不变。

## 7. 每个 invariant 的强制 TDD 回执

每个独立 invariant 必须记录：

1. SELECT INVARIANT 与 acceptance boundary；
2. WRITE TEST；
3. VERIFY RED，且失败原因是目标缺陷；
4. IMPLEMENT 最小生产改动；
5. VERIFY GREEN；
6. REFACTOR；
7. relevant 与 broader REGRESSION；
8. SCOPE CHECK；
9. RED/GREEN/test-output SHA、diff、migration 与 proof limits。

没有真实观察到 RED 的新增测试只能登记为 regression。每批完成不代表 finding 关闭，更不代表 GO。

## 8. Mandatory Counterfactual Suite v2

除旧公开 suite 外，至少必须逐项执行并记录 input、expected、actual、execution path、exact location 和 bypass analysis：

1. empty evidence cannot become supported；
2. missing counter-search cannot become Candidate；
3. Replacement cannot jump to Realized without production/scale gates；
4. non-quarter periods cannot satisfy quarter persistence；
5. knowledge revision cannot impersonate a new industry event；
6. governance bundle/child/trust-anchor/engine hash mismatch fails closed；
7. caller or researcher self-reported GateResult cannot replace authority computation；
8. default production composition makes every required state/control reachable；
9. one evidence item cannot duplicate primary high-tier credit；
10. controlled E2E traverses the actual company-scoring chain；
11. caller-controlled repository/ledger cannot authorize capability；
12. lexical polarity alone cannot authorize accounting/Earnings；
13. Stage 3/4/6 same input produces the same full-family result；
14. partial family recompute cannot preserve stale authoritative values；
15. different credentials/wrappers cannot substitute for distinct authenticated principals；
16. monitoring crash at every durable boundary is recoverable with the same operation ID；
17. `OUTCOME_UNKNOWN` reconciles before retry；
18. fixture authority/principal cannot satisfy live/release evidence。

每个关键状态迁移必须包含 positive、explicit negative、unknown、conflicted、missing-field、hash mismatch 和 Hard-Gate bypass。

## 9. Batch Receipt

每批 receipt 必须绑定：

- exact HEAD、完整 git status hash、target inventory before/after SHA；
- 修改文件及 pre/post SHA；
- writer、validator、reader、state-transition consumer；
- RED/GREEN/regression/compile/static-check 命令、退出码和输出 SHA；
- migration dry-run/apply/verify/rollback；
- fault injection、reconciliation 和未检查项；
- historical sentinel mismatch count；
- proof boundary。

目标 inventory 中 `protected_read_only` 文件任一 SHA 变化，当前批立即无效。

## 10. 实现后独立审核

全部 Batch 通过后才启动新的 Initial Independent Review。角色顺序固定为：

```text
fresh Reviewer A
→ independent Adjudicator
→ Developer only fixes ACCEPTED P0/P1
→ original Reviewer A closure
→ fresh-context Challenger（每轮都必须运行）
→ fresh-context Exit Auditor
→ hidden holdout
→ human final confirmation
```

Reviewer、Challenger、Adjudicator、Exit Auditor 必须使用独立 task/context 与不同 agent/session identity；第一遍均不得看到其他角色结论或开发者解释。无法建立独立上下文时最高状态为 `READY_FOR_EXTERNAL_REVIEW`。

新审核最多五个 Fix Cycle；上限不能强制转成 GO。同一根因连续三轮复现仍立即 `BLOCKED_REPEATED_ROOT_CAUSE`。

## 11. 退出门

### Implementation Complete

```text
all_batches_green == true
AND all_batch_receipts_complete == true
AND protected_predecessor_mismatch_count == 0
AND migration_verification_complete == true
AND controlled_e2e_actual_company_chain == true
AND controlled_e2e_uses_v2_authority_and_engine == true
```

只允许 `IMPLEMENTATION_COMPLETE_PENDING_INDEPENDENT_REVIEW`。

### Technical Exit

必须同时满足 mandatory file/risk/counterfactual coverage 100%、open P0/P1 为 0、false closure 为 0、Challenger 和 Exit Auditor 新有效 P0/P1 为 0、exact target unchanged、Exit Auditor fresh context。

未运行隐藏 holdout时最高为 `TECHNICAL_GO_PENDING_HOLDOUT`。

### Benchmark Exit

还需：hidden P0 recall 100%、P1 recall 达批准阈值、HBM regression、AI 数据中心电力/冷却 Top-10 复核，且每主题人工复核不超过 10 家。只有 fresh Exit Auditor 可输出 `BENCHMARK_QUALIFIED_GO`。

### Live/Release

Benchmark GO 不证明 live Tavily/DeepSeek、auth principal、计费、部署或用户可见结果。`RELEASE_READY` 还需 exact deployed SHA、production governance bundle、live principal/authority receipts、live Stage 1–7 terminal state 和 artifacts；最终交付需 `USER_VISIBLE_DELIVERY_VERIFIED`。

## 12. 停止条件

出现以下任一项立即停止并报告：

- 需要修改冻结评分语义或历史 benchmark；
- protected predecessor SHA 变化；
- target inventory 在审核期间变化；
- 需要新增尚未批准的外部权限、密钥、成本或部署；
- principal provider/IdP 无法提供可验证 attestation，且任务进入 live production；
- mandatory coverage 不完整或 finding 未 adjudicate；
- 独立 Reviewer/Challenger/Adjudicator/Exit Auditor 无法运行；
- 新审核同一根因连续三轮复现。

## 13. 当前证明边界

本 Program 冻结目标、范围、批次、RED probes 和退出门。它尚不证明任何 Batch 已实现、迁移可用、测试通过、controlled/live E2E 跑通、旧 finding 关闭、排名达标、GO、部署或用户可见交付。
