# Theme research: fact-directed retrieval repair (2026-09-14)

## Changes and evidence

- The direct Stage 3 Round Robin now asks dimension-specific factual questions
  about orders, production dependency, qualified supply, qualification duration,
  expansion lead times and substitutes. A subsequent gap iteration uses a different
  question and manufacturer-disclosure search. Product scope, region and as-of date
  remain in each query. The separate RPC candidate planner is unchanged.
- Background/ineligible cards can consume at most half of `max_sources`, rounded
  down, across the direct run. The remaining capacity is reserved for potentially
  scoring evidence. This is an admission limit, not automatic promotion of weak
  evidence. Time, cost and total card limits remain active; all scoring cards can
  still exhaust the total budget before another iteration.
- Original HTML publication-date verification now reads typed article/page JSON-LD,
  including `@graph`. An explicit URL/ID must match the fetched page. Dates attached
  to unrelated articles or organizations are ignored; modification-only metadata
  and conflicting publication dates do not establish a date. PDFs still require
  separate date provenance and are not silently made eligible.
- A real run demonstrated that `site:` text alone did not restrict Tavily results.
  Explicit site constraints are now sent as `include_domains`, and returned hosts
  are checked locally. Queries without a site constraint stay unrestricted.
  Domain selection is a discovery constraint, not proof that a claim is correct.
  See the [Tavily domain-filter documentation](https://docs.tavily.com/documentation/best-practices/best-practices-search).
- The operational runner now permits two scoring iterations and three search hits
  per request, keeping the 16-card, 900-second and configured USD 5 bounds. Provider
  dollar costs are not independently metered; these are not verified billing caps.

## TDD and replay

Observed RED before each production change: own-article JSON-LD date was missing
(two cases); unrelated article/organization dates were incorrectly accepted (two
cases); context cards prevented the second acquisition call; both iterations used
the dimension label instead of a factual query; the provider request omitted its
domain constraint. Each focused test passed after its corresponding implementation.
Modified-only and conflicting-date cases also passed as regression coverage.
The full Theme Chokepoint suite after the domain-filter repair passed **446 tests, two skipped** (136.62
seconds). The two skips are opt-in live calibration tests. Python compilation and
whitespace checks passed. These checks do not prove live source accessibility or
that extracted facts are true.

Replaying the saved original HTTP bodies from
`reports/theme-live-round-robin-20260914T041946Z` recovered the published dates
2026-06-04 (Fact.MR, original 001) and 2026-09-10 (Procurement Resource, original
003). No network or model output was needed for this replay. The former run's
immutable result was not rewritten. Publication metadata verifies a publisher's
date declaration, not the truth or fitness of its market claims.

## Live attempts

The intermediate run `reports/theme-live-fact-search-20260914T115106Z` exposed
Tavily's unfiltered results before the domain-parameter repair. It completed
Stages 1–3 with nine searches, 27 original fetch attempts and 31 model calls in
172.87 seconds. Its computed interval was 35–100, with downstream criticality
[4,4], qualification [1,4], capacity inelasticity [3,4] and the other dimensions
unknown [0,4]. It retained 16 cards (eight context-only); all-source qualification
flags alone must not be counted as actual scoring use. It remained incomplete.
This intermediate result is not evidence that the final version passed.

The domain-filtered run `reports/theme-live-fact-search-20260914T115407Z` executed
the second acquisition iteration, but stopped at its ninth search / 23rd fetch
because the active environment lacked `pypdf`, already declared in
`requirements.txt`. SEC and Congress responses were HTTP 403; DOE HTML and a
manufacturer PDF were actually reached. This is a failed attempt, not a completed
Stage 3 result. Installed pypdf 6.18.1 into the isolated `.tmp/shadow-test-deps`
runtime, then replayed the saved manufacturer PDF successfully (28,830 extracted
characters). No application fallback or weakening of source checks was added.

Run `reports/theme-live-fact-search-20260914T115741Z` then exposed PDF layout
whitespace in exact-quote validation. Both rejected quotes matched the independently
extracted PDF after whitespace normalization, but neither matched before it. PDF
page text now normalizes whitespace before hashing, storage and model input; page
boundaries remain separated. No substantive characters, quotes, ownership or date
eligibility rules are rewritten. The focused PDF test failed before the change
and passed afterward; replay of the saved PDF proved that both captured quotes
are now exact substrings. Final relevant acquisition/counter/Stage 3/orchestrator
regression passed 78 tests. The earlier full-suite count precedes this PDF change.

### Latest final-version attempt

`reports/theme-live-fact-search-20260914T120027Z` completed both acquisition
iterations (12 searches, 28 original fetch attempts, 23 model calls; 166.92
seconds). HTTP responses included 19 successes, eight 403s and one 429. The DOE
resilience-report PDF was successfully extracted. Eight cards reached the actual
fact-scoring input, compared with one in the previous pre-repair run.

It then stopped on the existing evidence-ownership guard. The model's
`qualification_barrier` facts cited `evidence_9706651488a226cd8fd7` (owned by
`substitute_weakness`) and `evidence_09a70cdc61d884b3bda9` (owned by
`capacity_inelasticity`). The exception's generic wording mentions primary
high-score ownership, but the actual failed check is resolved-dimension citation
ownership; no accepted high-score result is established by this exception.
`retrieval-audit.json` records the mismatches, HTTP outcomes and execution-source
hashes. The scoring contract hash by itself does not identify the retrieval code.

The latest attempt has **no valid final score**, no persisted Stage 3 result and
no counter-search execution. It must not inherit the intermediate run's 35–100
interval or any prior run's three counter-search receipts. The retrieval repairs
are implemented and verified; model citation assignment remains an unresolved
scoring-layer issue. Repair must preserve per-dimension ownership and the existing
validator, rather than accepting the rejected output or relabeling its sources.

All live attempts have isolated report databases, model responses and original
HTTP artifacts. Company/verification services remain unconfigured and Stage 6's
no-events adapter does not prove live monitoring. No Stage 4–7 live-success claim
or production promotion follows from this repair. Existing unrelated changes,
including the Chroma database, are preserved. Nothing was committed or pushed.
