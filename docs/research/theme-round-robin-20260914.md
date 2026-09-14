# Stage 3 Round Robin — 2026-09-14

The direct Stage 3 acquisition path now cycles over pending (node, material-field)
lanes. A lane contributes at most one unique original-source evidence candidate
per turn. Its unconsumed batch remains in an iterator for later turns; the search
is not repeated just to consume that batch. Empty lanes leave the queue, search
summaries are ignored, and duplicate facts do not consume a card slot. Missing
fields are recalculated at the next scoring iteration.

The existing source-card, elapsed-time and reported-cost limits still stop the
loop. Coverage of every lane requires enough budget; a two-card budget cannot
guarantee six lanes. This change does not add concurrency or change the separate
RPC candidate scheduler or the three-route counter-search protocol.

## Verification

Before implementation, the focused fairness test failed for budgets of 2, 6 and
16 cards: only 1, 2 and 4 dimensions respectively were searched when each returned
five candidates. After implementation all three passed, with one card per lane
before any second card. At 16 cards the controlled allocation is 3/3/3/3/2/2.

Additional regression cases cover empty lanes, all-empty results, duplicate
facts, excluded search summaries, exact card caps, provider receipts and cost
accounting. These additional cases were added after implementation and are
regression evidence, not separate RED/GREEN claims. The Stage 3 suite passed
40 tests. The complete Theme Chokepoint regression passed 436 tests with two
opt-in live calibration tests skipped (141.42 seconds). Python compilation and
`git diff --check` passed.

## Real execution

Artifacts: `reports/theme-live-round-robin-20260914T040301Z/`.

The bounded live runner used actual DeepSeek and Tavily requests, independently
fetched original pages, and retained the same 16-card / one-iteration bounds.
It completed Stages 1–3 in 84.70 seconds with 9 searches, 18 fetch attempts and
15 model calls. Requests 001–006 cover all six dimensions in order: demand
pressure, downstream criticality, effective supply concentration, qualification
barrier, capacity inelasticity and substitute weakness. Requests 007–009 are the
demand, supply and alternatives counter searches. The old run searched only the
first four positive dimensions before consuming its card budget.

The new run retained 16 cards, all context-only and ineligible for scoring.
All six dimension states and all three counter coverage states remain unknown.
It stopped with `INCOMPLETE_BUDGET_EXHAUSTED` /
`v161_state_eligibility_unproven`, without a runtime exception. Search opportunity
is established; successful evidence acquisition for each dimension is not.
Company/verification services remain unconfigured, and real Stages 4–7 did not
run. This is not a passing full E2E. API usage was real; aggregate dollar cost
was not independently verified.

Only `stage3.py`, its test file and this note were changed for Round Robin.
Earlier work and unrelated database changes were preserved; nothing was committed
or pushed.

## Prompt alignment and latest score

The active evidence-extraction system prompt in `providers/llm.py` now states
the Round Robin policy, one unique card per lane per turn, retained batch results,
runtime ownership of scheduling, and preservation of missing/context-only evidence.
Its version is `theme-chokepoint-evidence-span-v2-round-robin`.
The model-request contract test failed on the missing policy before implementation
and passed afterward. An earlier list/tuple mismatch in the test was corrected
and is not RED evidence. Relevant acquisition, counter, Stage 3 and orchestrator
regression passed 68 tests; compilation and whitespace checks passed. This test
proves prompt delivery, not live model compliance. No new live run followed this
prompt-only change.

The latest live run remains `reports/theme-live-round-robin-20260914T041946Z/`,
which used the Round Robin code before this prompt update. Its total interval is
0–100 out of 100; each of the six dimensions is unknown [0,4]. Decision coverage
is 0%, with no assigned segment state. Of 16 cards, one was scoring-eligible and
15 were context-only; the eligible card still did not establish a rating anchor.
All three counter coverage states are unknown. The run completed Stages 1–3 in
97.89 seconds with nine searches, 18 fetch attempts and 19 model calls, then
stopped with `v161_state_eligibility_unproven`. These bounds express unresolved
evidence, not an observed zero score or a passing E2E.
