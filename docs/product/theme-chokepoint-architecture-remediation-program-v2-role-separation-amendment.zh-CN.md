# TC-ARP-v2 Role Separation Amendment

```text
amendment_id: TC-ARP-v2-AMEND-ROLE-001
effective_date: 2026-08-18
authority: Financial Agent product owner
status: APPROVED_EFFECTIVE
```

## 1. 决策

Theme Chokepoint Architecture Remediation Program v2 的 Orchestrator 不得兼任 Developer。

本勘误只收紧执行角色隔离，不改变 ADR-0003、冻结 invariant、W1–W5、Batch 0–5、评分合同 v1.4、Golden、Holdout、P0/P1 定义或退出门。

## 2. 角色合同

```text
orchestrator_developer_mode = false
orchestrator_identity != developer_identity
one_dedicated_developer_may_execute_batches_0_through_5 = true
orchestrator_may_modify_runtime_or_tests_or_migration = false
developer_may_self_review_or_adjudicate_or_exit_or_sign_go = false
```

Orchestrator 只负责角色调度、只读状态检查、接收 sealed receipts、触发 machine validator、根据机器结果推进状态，以及非裁决性汇总。

Developer 负责严格 TDD 实施 Batch 0–5，并在 Fix Cycle 中只修复经独立 Adjudicator 接受的 P0/P1。Developer 可以使用同一个专用 Session 连续完成多个 Batch；本决策不要求每个 Batch 新建 Session。

Architecture Reviewer、Architecture Challenger、Design Adjudicator、Reviewer A、每轮 Challenger、Finding Adjudicator、Root Cause Adjudicator、Exit Auditor 与 Holdout evaluator 仍须满足各自的 fresh/independent context 合同，并与 Orchestrator、Developer 使用不同 identity。

## 3. Fail-closed 条件

以下任一情况发生时，当前推进必须停止并标记：

```text
INVALID_ROLE_SEPARATION
```

- `orchestrator_identity == developer_identity`；
- Orchestrator 修改了运行代码、测试、migration 或实现型 machine control；
- Developer 生成或签发自己的 review、closure、adjudication、challenge、exit 或 GO 结论；
- 缺失可持久化的 Orchestrator/Developer identity 证明。

恢复必须使用新的、可证明彼此独立的 identity，并重新捕获未受角色污染的 implementation snapshot。不得只修改角色名称或手填布尔字段。

## 4. 生效与优先级

本勘误自产品负责人批准时生效。凡 v2 Prompt、说明或回执中存在“Orchestrator 可以兼任 Developer”的旧语句，均由本勘误和完整 Session Prompt v2.1 取代。

本勘误不声称任何 Batch 已开始或完成，不证明测试、迁移、E2E、finding closure、GO、部署或用户可见交付。
