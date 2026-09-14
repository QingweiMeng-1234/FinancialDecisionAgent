# Stage 3 v1.6.1 shadow replay — 2026-09-13

The Stage 3 API now exposes an explicit evidence replay:

```python
receipt = loop.run_v161_shadow(
    existing_run_id,
    extractor=v161_fact_extractor,
    artifact_root=shadow_receipt_directory,
    shadow_id="trial-001",
    counter_executor=configured_counter_executor,
)
```

`loop` is an existing `EvidenceChokepointLoop`; the run must already have a
persisted Stage 3 result. `extractor` implements the existing v1.6.1 fact
extractor interface (including model, prompt/schema hashes and extraction
provenance). The caller chooses its configured provider/client. The new entry
does not select another model or run automatically. Full mode now requires a
counter executor with a stable, non-empty `identity` string and an `execute`
method returning `CounterSearchRawProviderResponse`. The executor must perform
actual retrieval; it must not declare a route negative merely because the
original evidence pack contains no counterexample.

## Implemented boundary

The entry reloads the Stage 3 result and groups cards by the complete atomic
AssessmentScope. It checks quote offsets against original text, source hashes,
source URLs, Claim membership and scope equality before inference. Empty or
duplicate evidence sets, ineligible/ambiguous evidence and company-scoped input
fail closed. A malformed group aborts the replay; it is not silently discarded.

Case-bound facts are extracted with the existing v1.6.1 contract, independently
validated, and scored again in code. Adapter-provided scores are retained only
inside extraction provenance and cannot control the resulting ratings. The
receipt preserves Claims, evidence, complete source context, scope, extraction
metadata, validated facts, six dimension bounds and the weighted score interval.

The receipt is a separate JSON file, explicitly `mode=shadow` and
`production_eligible=false`. It never calls canonical Stage 3 persistence or
updates run status. The existing `run()` and RPC candidate paths are unchanged.

Each shadow ID pins the baseline, model, prompt, schema, scoring contract and
recorded implementation hashes. Sequential retries reuse the verified receipt
without another provider call; changed inputs require a new ID. A checksum
detects stored-content corruption. A completed temporary file is flushed and
published through a non-overwriting atomic hard link, so a write failure cannot
expose a partial completed JSON receipt. Use an artifact filesystem supporting
hard links; unsupported publication fails rather than falling back to overwrite.

## Initial facts-only boundary (superseded for full mode)

The initial implementation was **evidence/fact/scoring replay**, not full v1.6.1 Stage 3 state-machine
integration. It does not reinterpret v1.4 critic receipts as authorization for
v1.6.1 gates. Every shadow segment therefore reports
`primary_state=null`, `state_status=withheld_pending_v161_critic`.

The counter-search correction below supersedes that limitation for full mode.
Explicit `facts_only=True` retains the initial withheld behavior. Existing release manifests
remain unchanged, and production continues on its existing path. The checksum is
a local integrity check, not provider authentication or independent semantic
verification. There is no guarantee of exactly-once provider execution under
concurrent calls or a process crash before publication; concurrent publication
cannot overwrite an existing receipt.

## TDD and verification evidence

Tests exercise the public Stage 3 entry with real SQLite persistence and the
existing controlled Stage 3 fixture/provider boundary.

| Invariant | Observed RED | GREEN |
| --- | --- | --- |
| Isolated persisted shadow replay | Stub returned no receipt | Bound facts and receipt persisted; canonical result/status unchanged |
| Quote/Claim/scope binding | Forged quote, wrong Claim and changed scope were accepted | Rejected before provider I/O |
| Evidence admission | Ineligible and empty evidence sets were accepted | Rejected before provider I/O |
| Sequential retry identity | Retry spent again then raised FileExistsError | One provider call; changed model rejected |
| Code owns scores | Forged adapter rating produced [4,4] instead of [0,4] | Deterministic fact score retained |
| Response case identity | Wrong-case response was accepted | Rejected without publishing |
| Cached receipt integrity | Altered score was reused | Checksum rejection |
| Atomic publication | Simulated disk failure left partial completed JSON | No completed JSON published |

Duplicate-evidence rejection is regression evidence: the existing extractor already
rejected it before the new entry added its own preflight check. The first test
attempt also encountered a test-helper import error, which was fixed before the
meaningful RED; that collection error is not counted as TDD evidence.

Final focused suite: **12 passed**. Relevant Stage 3, v1.6/v1.6.1 scoring and fact
adapters, source repository, production composition and orchestrator regression:
**103 passed**. This includes the existing controlled Stage 1–7 test, not a live
provider or deployed v1.6.1 end-to-end test.

The initial broader run had 102 passes and one Stage 7 failure because the shared
environment lacked the already-declared `pyarrow>=14.0`. PyArrow 25.0.1 was installed
under `.tmp/shadow-test-deps` only. With that directory plus `src` and `tests` on
PYTHONPATH, all 103 tests passed. No dependency declaration or shared environment
package was changed. Python syntax/whitespace and the frozen release verifier
also passed (21 frozen evaluation assets, 9 release assets).

A separate controlled smoke artifact is preserved under
`reports/stage3-shadow-v161-controlled-20260913T141859Z/summary.json`.
It records one scoped segment, six bound evidence cards, one provider fixture call
across two invocations, and unchanged canonical result/status. The receipt's
content checksum is
`8b20472fb83a03d51ad46ef9c9d778e4330723396c6a7342137b6787a1429dac`.
This smoke used controlled provider output; no new live model call was made.

The earlier 85% golden acceptance policy remains in force. The previously observed
gas-turbine fact-extraction instability is not changed or hidden by this integration.

## Counter-search correction after user review

The original narrowing omitted an expected research step. Passing the 85% golden
agreement policy does not establish that fresh counter-search occurred.

Full shadow now executes the existing demand, supply and alternatives searches
for every atomic scope. Requests, raw responses, parsed findings, new source
bytes, Claims and reconciliations are saved in a separate scope-bound SQLite
store. The critic reloads and verifies materialized counter evidence before
deriving the six gate-context fields. Original-source independence is reconciled
read-only against the original repository; an empty shadow store cannot silently
erase established source independence.

This reuses **fresh execution** of the existing `segment-counter-search-v1.4`
transport/critic protocol. It does not relabel or reuse a previously stored v1.4
receipt. Bounds are still computed from v1.6.1 facts. Gate/state math is delegated
to the existing frozen v1.6 evaluator through `materialize-calibration.mjs`.
The new receipt preserves the protocol version, new counter evidence, gate
context and complete deterministic state calculation. A found counterexample
blocks candidate states through mandatory conflict/direct-evidence gates; it
does not silently rewrite the original fact scores.

Without an executor, full mode fails before model inference. Facts-only mode
must be explicit. Completed sequential retries reuse the receipt without another
model/search call; executor identity, mode or graph/query-scope changes cannot
reuse that receipt. Failed searches leave their partial audit store and publish
no completed assessment; restarting such a run requires a new shadow ID.

Observed TDD evidence:

| Invariant | RED observed | GREEN |
| --- | --- | --- |
| Three fresh routes and evaluated state | Zero executor calls | Three reconciled routes; positive fixture becomes candidate |
| No silent omission | Missing executor did not raise | Rejected before inference |
| Original-source reconciliation | No original-store reconciliation calls | One supply-credit source checked (fixture expectation initially corrected from two to one) |
| Stable transport identity | Anonymous executor accepted | Rejected before spending |
| Search scope belongs to retry identity | Changed graph reused receipt | Changed graph rejected without new searches |

Regression checks additionally cover each route producing new, materialized
counter evidence, unknown coverage, and completed retry reuse. These tests
passed against the reused critic behavior on their first run; they are not
claimed as newly observed RED/GREEN proofs. All three found-counter scenarios
and an unknown-demand scenario prevent candidate/strong-candidate states.

Final related Python suite: **125 passed** (including 22 shadow tests and 12
counter-provider tests). This is controlled execution against real local
persistence, not proof of live retrieval or deployment. The production builder
still requires an externally supplied `counter_search_executor`; this turn
did not configure or call a live search transport. Node must be available for
frozen state calculation. Existing search budgets apply per scope, with cost
checked after returned route responses; transport timeouts/spend enforcement
remain the executor's responsibility. Production promotion remains disabled.

The five-scenario controlled smoke is preserved at
`reports/stage3-shadow-counter-controlled-20260913T144736Z/summary.json` with
scope-local receipts and audit databases. Each scenario made one controlled fact
call and three controlled counter calls across two invocations. At score_min
72.5, the all-negative fixture became a candidate; each found-counter scenario
and the unknown-demand scenario withheld the state. All canonical results stayed
unchanged. This artifact explicitly records `live_search=false`.

Final static/release verification: Python compilation and `git diff --check`
passed; the three materialization Node tests passed; the frozen verifier checked
21 evaluation assets and the release verifier checked nine release assets.
