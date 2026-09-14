# Theme-Research calibration repair — 2026-09-13

This repairs the calibration entry used in the first live run. The direct-scoring
prompt experiments remained unstable; the fact path instead reuses the existing
v1.6.1 fact contract and deterministic scorer without changing either.
The frozen task, evidence, adjudicated answers, scoring contracts and active v3
thresholds remain unchanged. Work is based on commit
`400ebb4968f4b5901d48a64171767aa642de977d`, branch
`codex/fix-theme-golden-calibration`.

## Changes

- The experimental direct-scoring procedure explicitly separates permanent removal of the whole scoped
  Segment from the largest-supplier 90-day outage. It distinguishes source-bound
  current designs from unrelated projects in the broader end market, and permits
  an explicit dependency bridge across case-bound evidence.
- `materialize-calibration.mjs` uses the frozen validator's formulas to produce a
  separate `computed-response.json`. Only derived numeric metrics can change;
  ordinal evidence, bounds, gates, final states and rationales stay unchanged.
  Other validation errors fail closed, and an existing output cannot be overwritten.
- The runner preserves raw JSON, prompt, schema, events, hashes, requested model,
  CLI version and completion evidence. Comparison reports apply the current v3
  policy; the historical agreement calculator contributes formulas only.
- `--fact-only` uses the existing v1.6.1 fact prompt and strict Pydantic schema.
  `compile-fact-calibration.py` validates case binding and facts with the existing
  decoder/scorer, then uses the frozen validator to derive gates and final states.
  It never consumes the adjudicated reference. The comparison step reads that
  reference only after compilation. This evaluates a different system from the
  earlier raw direct-scoring calls; do not attribute improvement to prompting alone.

## Reproduce in PowerShell

Use an authenticated compatible Codex CLI (tested with 0.153.4) and the repository
Python environment. `--cli` takes an executable path. The live command uses the
existing login and consumes model usage.

```powershell
python tools/theme-chokepoint/run-calibration-once.py --fact-only --cli 'C:/path/to/codex.exe'
# Set this to RUN_DIR printed by the command above.
$run = 'reports/theme-golden-live-YYYYMMDDTHHMMSSZ'
python tools/theme-chokepoint/compile-fact-calibration.py $run
python tools/theme-chokepoint/compare-calibration.py $run --computed
$env:THEME_GOLDEN_RUN_DIR = (Resolve-Path $run).Path
python -m pytest tests/test_theme_chokepoint_live_calibration.py -q
```

Read `status` and `gates` in the comparison JSON; the reporting script's process
exit code is not an acceptance gate. Raw reports and computed reports have separate
filenames. The live regression test is opt-in; without the environment variable
it skips and does not prove model behavior.

## Evidence recorded during development

- Structural regression: observed RED on the original completed model response
  (three of four downstream criticality judgments differed from the reference).
- Numeric invariant: observed RED with a clone-only implementation, where
  decision coverage remained 0.9 instead of the frozen formula's 0.5; the minimal
  recomputation then passed GREEN.
- Evidence binding invariant: observed RED when an outside-case evidence ID was
  accepted; rejecting non-arithmetic validator errors then passed GREEN.
- The arithmetic test also checks that inputs, ordinal evidence, hard gates and
  non-metric state fields remain unchanged. These assertions are regression
  evidence for preservation, not a separately claimed RED/GREEN cycle.
- Relevant Python scoring and inference adapter regression: 33 passed.
- Fact-path invariant: observed RED with a clone-only implementation (no derived
  state); reusing the frozen validator then produced the expected watch state,
  three passing gates and coverage 0.5 (GREEN).
- Relevant Node validator, agreement and materializer regression: 87 passed.
- Python syntax, Node syntax and whitespace checks passed. Frozen release checks
  passed for the 21 locked assets and 9 release assets.

Generated artifacts are retained locally under `reports/` (git-ignored). The
pre-existing `chroma_data/chroma.sqlite3` change is outside this repair.

## Live outcomes before the owner threshold update

All calls used `gpt-5.6-terra`, reasoning `high`, CLI 0.153.4. Run names below are
under `reports/theme-golden-live-20260913T<time>Z`.

| Run | Entry | Structural reference matches | Gate matches | State matches | Outcome |
| --- | --- | --- | --- | --- | --- |
| 123107 | Original direct scoring | 1/4 | 10/12 | 1/4 | FAIL |
| 124312 | Structural guide | 3/4 | 12/12 | 3/4 | Raw arithmetic FAIL; computed v3 gates PASS, structural regression still RED |
| 124824 | Guide plus evidence composition | 3/4 | 11/12 | 2/4 | FAIL |
| 125319 | Guide plus current-design scope | 2/4 | 10/12 | 1/4 | FAIL, including arithmetic errors |
| 125749 | Existing v1.6.1 facts plus deterministic scoring | 4/4 | 11/12 | 3/4 | Structural regression GREEN; overall v3 FAIL |

The final fact-path report is
`reports/theme-golden-live-20260913T125749Z/computed-report.md`.
Its complete ordinal tuple agreement is 17/24; kappa is 1 on only five exact/exact
pairs. No candidate-boundary disagreements occurred. The final response SHA-256 is
`d8836a80cd69af99a0aa34120d2c4f2204745ee8f6acedd31adc960b162f51ab`;
the preserved raw fact response SHA-256 is
`4bc379347476d4999ac9ecdadbc8173a80060080c16f8a589d546fcf01ad413c`.

The remaining failing gate is gas-turbine Segment Discovery. The reference accepts
the broader U.S. turbine order pipeline's five-year CAGR as a conservative demand
floor for large turbines serving data centers. The existing fact extractor leaves
that dimension unknown because the series is not split into that narrower scope.
The frozen overlay explicitly permits an annual capacity-expansion demand proxy,
but does not explicitly resolve this broader-market historical order proxy.
Changing scores to match the answer would conceal this policy gap. No new proxy
exception, reference adjustment, threshold relaxation or production promotion was
made. The owner needs to resolve that scope/proxy boundary before claiming overall
calibration acceptance. Other ordinal/provenance disagreements remain visible in
the detailed report.

Final verification: the selected fact-path structural test passed (1 test), along
with the 33 Python and 87 Node regression tests above. This is a partial repair:
structural handling and deterministic calculation are verified on this run; the
full golden acceptance gate remains failing.

## Owner threshold update — 2026-09-13

The owner subsequently requested hard-gate agreement of at least 85% instead of
95%. Policy v4 records this change; all other thresholds are unchanged. The
comparison entry now uses v4 and writes `computed-v4-*` / `v4-*` reports, preserving
the earlier v3 policy, reports and frozen release assets.

Re-evaluating the same `125749` fact response under v4 passes every acceptance
gate: hard gates 11/12 (91.7%), final states 3/4 (75%), kappa 1 on five exact pairs,
and zero high-risk boundary disagreements. This is a threshold re-evaluation,
not a new model call or a change to the remaining gas-turbine demand judgment.
The detailed result is
`reports/theme-golden-live-20260913T125749Z/computed-v4-report.md`.

## Local shadow preflight — 2026-09-13

The subsequent local `smoke-staging-v1.6.8.py` check passed: the isolated scoring
contract loaded, the qualification smoke produced [4,4], and the release was in
shadow mode with production disabled. `verify-release-v1.6.8.mjs` also passed,
verifying 21 frozen evaluation assets and 9 release assets. These commands inspect
local configuration and exercise local scoring; they do not contact a deployed
staging service or prove mirrored traffic is running.

The remaining integration boundary is concrete: `production.py` constructs
`EvidenceChokepointLoop`, while `stage3.py` declares the v1.4 scoring contract.
The release configuration explicitly marks v1.6 Stage 3 as `not_integrated`.
The fact calibration bridge has not changed that boundary. Runtime integration
will need versioned fact-to-assessment mapping, preservation of evidence and
claim bindings, and tests through Stage 3 persistence before a shadow exit report
can claim full-pipeline coverage.

## Fresh fact-path repeat — 2026-09-13 13:25 UTC

Run `reports/theme-golden-live-20260913T132551Z` made a new model call, with a
distinct completed session and identical prompt, runner, input and policy hashes
to the earlier fact-path evaluation. Compilation and mechanical validation passed.
The v4 acceptance gate passed again: hard gates 11/12, final states 3/4, kappa 1
on four exact pairs, and zero high-risk boundary disagreements. Thus both fact-path
calls pass v4, but this is only two calls on the same known calibration data.

The strict structural regression test **failed** on the repeat: gas-turbine item
`H16R6-O02-02` changed from supported/exact [4,4] to unknown/none [0,4]. The other
23 of 24 ordinal tuples were unchanged. The model again withheld a dependency
claim for the narrower data-center project scope. This refutes a claim that the
earlier 4/4 structural result is repeatably proven; the arithmetic code remains
deterministic for the supplied facts. The test and threshold are left unchanged.

The fresh computed response SHA-256 is
`14706afc0e39671ab655c7cc092da02d3593d054af7f3233e581d9fefdd3213b`.
The raw fact response SHA-256 is
`07cbd727c7baba7265f4fd9c033113c63f4dcf34b7fcca0bce4fe91350f7863d`.
The detailed acceptance report is `computed-v4-report.md`; `repeatability.json`
records the comparison and the failed stricter regression separately. These files
live in the new run directory. The calibration includes former holdout content,
so this diagnostic was not appended to the rolling shadow pool.

## Interpretation

These are calibration development calls on known data. Each isolated model call
omits adjudicated answers and uses no tools or external retrieval, but the human
developer/agent has inspected disagreements and tuned the procedure. This is not
an untouched blind evaluation or evidence of unseen generalization. Preserve and
report unsuccessful attempts as well as successful ones. Passing a selected run
does not prove repeatability, production end-to-end behavior, or promotion beyond
the existing staging/shadow release boundary.
