# Repository Development Instructions

## TDD requirement for development code

All changes to application code that add or modify runtime behavior must use strict test-driven development.

This includes:

- features and business rules;
- bug fixes;
- data-contract and persistence behavior;
- API behavior;
- refactoring that changes executable code;
- ingestion, retrieval, deduplication, validation, and idempotency logic.

Use this workflow for each independently testable invariant:

1. **SELECT INVARIANT** — state the behavior and acceptance boundary.
2. **WRITE TEST** — add the smallest focused test that proves the invariant.
3. **VERIFY RED** — run it and confirm it fails for the intended missing behavior, not because of an unrelated environment or fixture error.
4. **IMPLEMENT** — make the minimum production-code change needed to satisfy the test.
5. **VERIFY GREEN** — rerun the focused test and confirm it passes.
6. **REFACTOR** — improve structure without changing the proven contract.
7. **REGRESSION** — run the relevant work-package suite, then broader regression in proportion to risk.
8. **SCOPE CHECK** — inspect the diff and ensure unrelated user changes are not included.
9. **RECORD EVIDENCE** — report RED, GREEN, regression, static-check, and known proof limits honestly.

A test written after an implementation is post-hoc regression evidence, not proof of TDD. Do not describe it as RED/GREEN evidence unless the expected RED was actually observed.

## Work that does not require TDD

TDD is not mandatory for work whose primary outcome is operational or documentary rather than application behavior, including:

- deployment diagnostics and deployment-state inspection;
- deployment configuration repair;
- infrastructure and environment fixes;
- CI/CD wiring and release checks;
- documentation-only changes;
- read-only audits, reports, and one-off data inspection;
- operational scripts used only for a controlled one-time repair.

These changes still require verification appropriate to their risk, such as configuration validation, dry-run output, smoke tests, health checks, deployed-SHA checks, or read-only reconciliation.

For mixed tasks, apply strict TDD to the application-code portion and operational verification to the deployment or infrastructure portion.

## Evidence boundaries

- A focused GREEN proves only its tested invariant.
- Unit or integration tests do not prove deployment success, external-provider behavior, production data correctness, or user-visible delivery.
- Deployment health does not replace application tests.
- Preserve existing user changes and stage only files owned by the current task.
