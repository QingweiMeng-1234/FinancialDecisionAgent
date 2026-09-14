# Theme Research live E2E integration — 2026-09-13

## Implemented scope

The opt-in `enable_v161_segment_chain` mode now runs v1.6.1 fact extraction in
the ordinary Stage 3 acquisition loop. Code recomputes every ordinal from the
validated facts and uses the frozen evaluator for state math. Stage 4 and the
root manifest retain the same explicit composite identity:
`theme-chokepoint-staging-segment-v1.6.1-company-v1.4`.

This is a staging composition: Segment rules are v1.6.1; Company scoring rules
remain the governed v1.4 overlay. It is not a production promotion. Existing
v1.4 callers retain their defaults. The separately approved 85% golden agreement
threshold is unchanged and is not a live-research score threshold.

`OriginalSourceCounterExecutor` connects Tavily discovery, independent original
fetching and span extraction to the existing reconciled three-route critic.
The production composition can obtain this executor from its original-source
Tavily acquirer. It never interprets an empty search as explicit negative proof.
Its trace identifies this local adapter execution, not a fabricated upstream
Tavily request ID; unreported dollar cost is explicitly marked unknown in raw
adapter provenance.

The operational `tools/theme-chokepoint/run-live-e2e.py` entry records real model
requests/responses, search discovery results, original HTTP bytes/metadata,
extracted text, SQLite lineage and stage manifests in an isolated run directory.
It uses the user-provided Tavily credential and the existing DeepSeek credential
from an explicitly supplied `.env` path. Secrets are not written to artifacts.
The requested model alias is `deepseek-chat`; observed responses resolve to
`deepseek-flash`, as preserved in the raw model receipts.

## Live-discovered repairs

1. Unverified search-result publication dates formerly produced a contradictory
   candidate: `scoring_eligible=False` but `scoring_use=primary`. Stage 3 rejected
   it with `ineligible claim cannot be used as scoring evidence`. Such material
   is now retained as context. Explicit publication metadata from the refetched
   HTML can establish the original date; modification dates and conflicting
   publication dates cannot. No URL-date guessing was added. PDFs without such
   verification remain context.
2. The runtime fact scorer skips ineligible/context-only evidence. An entirely
   empty scoring ledger remains six unknown dimensions without a model call.
3. Live unknown dimensions have no scoring citation. The unfrozen bridge now
   permits exactly empty unknown citations and empty non-passing gates in an
   explicit runtime mode. Frozen calibration retains strict citation validation;
   resolved ratings, invalid IDs and other structural errors are still rejected.
4. An unestablished v1.6.1 state stops before Company research even when the
   extracted ordinal bounds are otherwise resolved.
5. A real demand-growth quotation was misclassified as counter evidence because
   it contradicted the negative search phrase (“slowdown”), rather than the
   original demand-pressure thesis. Counter adapter v3 explicitly supplies the
   original thesis, downstream theme and customer scope and defines stance against that thesis. The old
   counter item is rejected in a separate semantic-review artifact; its original
   receipt was preserved rather than rewritten.
6. A fresh counter source no longer has to borrow an already existing main-thesis
   evidence ID. It must still materialize its own independently quoted source and
   pass reconciliation. Main-thesis scoring credit stays empty when unavailable;
   the explicit-negative rule is unchanged.

## TDD and verification evidence

Meaningful RED failures were observed before implementing each of: original-source
counter transport; absent-quote rejection; future-publication exclusion; automatic
counter composition; runtime fact scoring; empty initial acquisition; Stage 3
v1.6.1 wiring; Stage 4/root lineage; public composition mode; cross-scope preflight;
runtime citation gaps; withheld-state stopping; context-only source handling;
publisher-date verification; counter stance orientation and downstream scope;
and independent new counter discovery without preexisting main credit. The corresponding
focused GREEN checks passed. Intermediate syntax/date-serialization errors are
implementation mistakes, not additional TDD evidence.

The final complete Theme Chokepoint suite passed **427 tests, 2 skipped** after
the live-discovered repairs. Both controlled
Stage 1–7 modes (legacy and v1.6.1) passed with real SQLite/artifact/export services
and controlled provider responses. The five Node materialization tests passed.
Python compilation and whitespace checks passed. The frozen/release verifiers
confirmed 21 frozen evaluation assets and nine release assets; production remains
disabled.

## Real-run evidence boundaries

- Initial live startup failed with DeepSeek HTTP 402. After the user recharged,
  actual model calls succeeded; Tavily also passed an independent live query.
- `reports/theme-live-e2e-20260913T173750Z` reproduced the publication-eligibility
  bug after two original fetches. This failed run is not counted as completed E2E.
- `reports/theme-live-e2e-20260913T174206Z` completed Stages 1–3 with seven actual
  searches, 14 original fetches and 19 successful model responses. It stopped as
  `INCOMPLETE_BUDGET_EXHAUSTED`, with six unknown dimensions. Its sole reported
  demand counter item was rejected on semantic review as described above.
- `reports/theme-live-e2e-20260913T174829Z` exposed the separate preexisting-credit
  requirement after three real search routes; this is preserved as a failed run.
- The final attempt, `reports/theme-live-e2e-20260913T175404Z`, completed Stages 1–3
  without a runtime exception after seven searches, 14 fetches and 18 successful
  model calls (including one v1.6.1 fact call). It lasted 81.18 seconds. All six
  scoring dimensions and all three counter coverage states remained unknown;
  no candidate label or new counter evidence was published. The status is
  `INCOMPLETE_BUDGET_EXHAUSTED`, reason `v161_state_eligibility_unproven`.
  Sixteen cards were retained, only two eligible for primary scoring. The bounded
  acquisition stopped after four dimension searches; the other three searches
  were the required counter routes. This is a completed live attempt, not a
  successful full Stage 1–7 E2E. See its `audit.json` and `report.md`.

The runner deliberately reports six Company/verification services as unavailable.
It cannot claim authenticated Company discovery, acquisition, scoring, critic,
business-fact verification or source-identity verification without those services.
Stage 6 is the existing no-events stage, not a live monitoring exercise. No
Stage 4–7 live-success claim follows from the controlled E2E tests.

The inherited critic remains conservative: found counter evidence and unknown
coverage do not establish candidate eligibility. The source-date parser verifies
publisher metadata, not the truth or scope of every source claim. Bounded searches
can miss decisive sources. Token usage is recorded; unreported API cost is not
treated as evidence of free execution or a verified aggregate dollar cap.

All live runs use isolated report databases. The existing dirty Chroma database,
golden evidence pack and frozen assets were not changed. No commit or push was
performed.
