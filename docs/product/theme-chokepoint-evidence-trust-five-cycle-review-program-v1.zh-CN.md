# Theme Chokepoint Evidence Trust 五轮审核程序 v1

## 1. 决策状态

```text
review_program_id: theme-chokepoint-evidence-trust-review-v1
status: FROZEN_FOR_CYCLE_1_START
authorized_on: 2026-08-17
maximum_fix_cycles: 5
current_cycle: 1
old_fix_loop_cycle4: prohibited
```

产品负责人决定：使用 ADR-0002 的新证据信任架构重新开始一个最多五轮的 Fix Cycle 审核程序。

本文件是 `theme-chokepoint-evidence-trust-remediation-program-v1` 的产品决策附录，只 supersede 原合同第 10 节中“全部实施批次完成后才启动 Initial Independent Review”的时序。原合同的评分语义、授权范围、RED probes、退出门、历史资产保护和 proof boundary 继续有效。

## 2. 与旧 Fix Loop 的关系

- 旧 Fix Loop 永久终止在 Cycle 3：`BLOCKED_REPEATED_ROOT_CAUSE`；
- 本程序的 Cycle 1 不是旧 Cycle 4；
- 旧五个 finding 和三轮 escape history 全部继承，不清零、不降级；
- 新计数只表示新的 architecture authority、target baseline 和 review exit gate；
- 历史 Cycle 1–3 报告、Golden、Holdout、Top-10 和 v1.4 freeze 保持只读。

## 3. 为什么现在开始 Cycle 1

Batch A 已产生部分 Python 实现和真实 RED/GREEN 证据，但 Node parity、production verifier composition、SourceIdentity、counter materialization、四角色 company chain 和 canonical monitoring 仍未完成。

把当前状态封存为 Cycle 1 `before_snapshot` 有三个好处：

1. 后续改动都有明确的 before/after 边界；
2. Reviewer 能在每轮检查问题是否真正迁移或关闭，而不是等到所有实现完成后一次性发现架构旁路；
3. 每轮都强制执行 Reviewer A → fresh Challenger → Adjudicator → fresh Exit Auditor，不让 Developer 自己宣布完成。

已经发生的 Batch A 修改是 Cycle 1 起始基线的一部分，不倒签为 Cycle 1 developer change。Cycle 1 developer receipt 只记录起始快照之后的修改。

## 4. 初始 accepted findings

Cycle 1 从以下 adjudicated findings 开始：

| Finding | Severity | Cycle 1 状态 | 新架构验收方向 |
|---|---:|---|---|
| C1-P0-002 | P0 | OPEN | 独立 verifier + exact governance SHA + Python/Node 同源授权 |
| C1-P1-001 | P1 | OPEN | counter Claim/Card/SourceVersion 物化 + 全局 replay 防护 |
| C1-P1-002 | P1 | OPEN | discovery/evidence/scoring/critic 四个低层 client 与独立 receipts |
| C1-P1-004 | P1 | OPEN | official accounting fact + 可比季度 lineage + production writer |
| C1-P1-005 | P1 | OPEN | immutable source provenance + canonical scorer 唯一 after-state 写权 |

Batch A 的局部 GREEN 只是 finding closure 的必要证据，不足以把 C1-P0-002 或 C1-P1-004 标为 CLOSED。

## 5. 五轮状态机

```text
CYCLE_n_START
→ ADJUDICATE_OPEN_FINDINGS
→ DEVELOPER_FIX_ADJUDICATED_P0_P1_ONLY
→ CREATE_AFTER_SNAPSHOT
→ REVIEWER_A_CLOSURE
→ FRESH_CHALLENGER_n
→ ADJUDICATE_CHALLENGER_FINDINGS
→ FRESH_EXIT_AUDITOR_n
→ CYCLE_DECISION
```

`n ∈ [1,5]`。五轮是最大修复次数，不是必须机械跑满：

- 若某轮完整满足技术退出门，可以提前进入 hidden holdout；
- 若 hidden holdout 通过，可以提前输出 `BENCHMARK_QUALIFIED_GO`；
- 若同一错误机制在新程序连续三轮复现，立即 `BLOCKED_REPEATED_ROOT_CAUSE`；
- 第五轮仍有 open/new P0/P1 时，输出相应 `BLOCKED_MAX_CYCLES_*`；
- 不得为了结束第五轮降低严重度、删除 probe 或修改 Golden/Holdout。

## 6. 每轮允许的 Developer 工作

每轮只处理该轮已 adjudicate 的 P0/P1。ADR 的 Batch A–E 是实现依赖图，不是固定的一轮一个 Batch；同一 Cycle 可以完成多个 Batch，也可以因真实依赖跨轮继续，但必须在 receipt 中说明。

禁止：

- 处理未 adjudicate finding；
- 修改 v1.4 评分语义；
- 修改历史 Golden/Holdout/freeze；
- 以 test-local completed domain object 冒充 production path；
- 只改测试预期、skip 或 fixture 答案；
- 把旧三轮 escape history 清零；
- 将“测试通过”写成 finding closure 或 GO。

## 7. 每轮强制角色

### Developer

严格 TDD 修复已裁定 P0/P1，提交 RED/GREEN/regression/migration/scope receipt，无 closure authority。

### Reviewer A

逐项复核旧 finding 的原反例、negative、unknown、conflicted、missing-field、hash mismatch、Hard-Gate bypass 和所有平行入口，无最终 GO authority。

### Fresh Challenger

每轮必须 fresh context；第一遍看不到旧 finding、Developer 解释和 Reviewer A 报告。先独立寻找 P0/P1，封存后再做 diff。

### Adjudicator

把 Challenger finding 分类为 `NEW_VALID_P0`、`NEW_VALID_P1`、`DUPLICATE`、`FALSE_POSITIVE`、`SEVERITY_DISAGREEMENT`、`COVERAGE_GAP` 或 `SAME_ROOT_CAUSE_DIFFERENT_PATH`。

### Fresh Exit Auditor

只有 Reviewer A 关闭旧 P0/P1、Challenger 无新增有效 P0/P1、coverage 完整且 target SHA 未变时才运行。第一遍不得看到前述结论，并主动构造最小反例。

无法提供 fresh-context Reviewer/Challenger/Exit Auditor 时，最高状态为 `READY_FOR_EXTERNAL_REVIEW`，不得 GO。

## 8. Cycle 1 起始边界

Cycle 1 before snapshot 必须包含：

- 当前 Theme Chokepoint runtime、repository、providers、CLI 和 focused tests；
- ADR-0002、原 remediation contract 和本五轮附录；
- exact runtime governance bundle 及所有 child controls；
- Cycle 2 RCA、Cycle 3 termination receipts 的只读引用；
- program manifest/freeze receipt 和 Batch A progress receipt；
- 不包含旧 runtime-conformance 目录作为当前实现证明。

Cycle 1 start receipt 必须明确：

```text
accepted_P0 = [C1-P0-002]
accepted_P1 = [C1-P1-001, C1-P1-002, C1-P1-004, C1-P1-005]
before_snapshot_id = exact Cycle 1 start inventory hash
developer_changes_after_snapshot_only = true
reviewer_a = pending
challenger_1 = pending
exit_auditor_1 = pending
go = false
```

## 9. Cycle Decision

每轮只允许：

- `CONTINUE_NEXT_CYCLE`；
- `CANDIDATE_FOR_HOLDOUT`；
- `BLOCKED_REPEATED_ROOT_CAUSE`；
- `BLOCKED_MAX_CYCLES_*`；
- `BLOCKED_NEEDS_PRODUCT_DECISION`；
- `READY_FOR_EXTERNAL_REVIEW`；
- `INVALID_CYCLE`。

Reviewer A、Developer、测试套件或 receipt-shaped object 均无权直接输出 GO。

## 10. 退出门

技术退出继续使用原 remediation contract 第 11 节全部条件，包括：mandatory file/risk/counterfactual 100%、P0/P1 清零、false closure 为零、fresh Challenger/Exit Auditor 无新增有效 P0/P1、exact target SHA 不变。

Hidden holdout、HBM regression、AI 数据中心电力/冷却 Top-10 人工复核继续是 `BENCHMARK_QUALIFIED_GO` 的必要条件。Live Tavily/DeepSeek、部署 SHA 和用户可见交付继续使用独立 release gate。

## 11. 当前证明边界

本附录只批准并冻结新的五轮审核程序和 Cycle 1 启动。它不证明 Cycle 1 developer work 已完成，不关闭任何 finding，不证明 controlled/live E2E，不证明 benchmark、部署或用户可见交付。
