# Factor And Chokepoint Evolution Roadmap

## Purpose

This document captures a practical evolution path for turning the current `financial-agent` stack into:

- a better narrative and supply-chain research engine
- a Serenity-style chokepoint analysis workflow
- a structured signal producer that can be validated by `QuantGPT`

It is a reference roadmap, not a fixed implementation contract.

## Current State

Today the repo is strongest at:

- news ingestion and refresh
- retrieval over a local article corpus
- ticker-level evidence gathering
- watchlist triage and review
- lightweight entity and theme awareness

Today the repo is weak at:

- theme-first research workflows
- supply-chain mapping
- chokepoint scoring
- structured factor datasets
- quantitative validation and backtesting inside the default path

In other words: the current system can explain why a ticker may deserve attention now, but it is not yet a reliable factor research platform or a full chokepoint research bench.

## Design Principles

### 1. Keep research and validation separate

Use `financial-agent` for:

- narrative formation
- evidence collection
- event and theme structuring
- supply-chain mapping
- candidate target discovery

Use `QuantGPT` for:

- factor definition
- panel construction checks
- IC and RankIC analysis
- bucket tests
- holding-period return tests
- industry-neutral and size-neutral robustness
- correlation vs existing known factors

Do not make `QuantGPT` the default path for ordinary research. It should validate structured hypotheses, not replace the research engine.

### 2. Do not start chokepoint work from a ticker list

For Serenity-style work, start from:

- a theme
- a supply chain
- a bottleneck segment

Only map to companies after the chain is clear.

### 3. Do not treat raw LLM prose as a factor

Any signal that will be tested by `QuantGPT` should first be converted into stable, structured fields.

### 4. Persist research assets before building fancy UI

Before building dashboards, persist:

- theme cards
- supply-chain maps
- evidence logs
- monitoring triggers
- structured per-ticker and per-segment features

## Target End State

The long-term workflow should look like this:

1. `financial-agent` discovers narrative shifts, fresh evidence, and relevant entities.
2. `serenity-chokepoint-analysis` maps the chain and identifies true bottlenecks.
3. `financial-agent` maps chokepoint segments to candidate companies and supporting evidence.
4. `QuantGPT` tests whether the resulting structured signals behave like a useful factor.

This creates three distinct outputs:

- research memo
- chokepoint thesis
- factor validation report

## Recommended Evolution Order

## Phase 1: Theme-First Research Foundation

Goal: extend the current ticker-first system into a theme and segment research engine.

### What to add

- a `theme_research` workflow
- theme-level retrieval and summarization
- supply-chain map artifacts
- theme-linked company discovery
- structured evidence typing

### Suggested outputs

- `theme-card.md`
- `supply-chain-map.md`
- `evidence-log.md`
- `monitoring-triggers.md`

These should live under:

`docs/research/serenity-chokepoint-analysis/`

### Structured evidence types to introduce

- `demand_expansion`
- `capacity_addition`
- `pricing_power`
- `qualification_barrier`
- `policy_support`
- `customer_concentration`
- `substitution_risk`
- `route_change_risk`

### Why this phase comes first

Without a theme-first layer, chokepoint analysis will collapse back into ticker commentary, and factor research will have no clean research object to validate.

## Phase 2: Chokepoint Analysis Workflow

Goal: make chokepoint work a first-class workflow rather than an informal research style.

### Core workflow

Follow this sequence:

1. define the narrative
2. explain demand expansion in one sentence
3. map the supply chain as `end system -> core component -> subcomponent -> equipment -> material -> raw material`
4. identify candidate chokepoints
5. separate technical importance from profit importance
6. assess moat, customer dependence, supply elasticity, and value capture
7. falsify the thesis
8. discuss valuation last

If step 2 cannot be expressed clearly in one sentence, mark the case as `needs narrative clarification` and stop deeper work.

### Chokepoint criteria

Each segment should be checked against the same six criteria:

- irreplaceable
- very few suppliers
- long qualification cycle
- slow capacity expansion
- high switching cost
- downstream stoppage risk if supply breaks

Rule:

- `4/6+` => `candidate chokepoint`
- `<4/6` => watch item only

### Suggested structured fields

At the segment level:

- `narrative_status`
- `candidate_chokepoint`
- `chokepoint_criteria_hits`
- `technical_importance`
- `profit_importance`
- `supply_elasticity`
- `customer_dependence`
- `value_capture`
- `thesis_fragility`

At the company level:

- `company_role`
- `segment_exposure`
- `direct_chokepoint_exposure`
- `indirect_chokepoint_exposure`
- `qualification_advantage`
- `capacity_constraint_exposure`
- `pricing_power_support`
- `route_change_risk`

### Expected result

At the end of this phase, the system should be able to answer:

- what segment is the bottleneck
- why it may be a real chokepoint
- which companies sit closest to the value capture point
- what would falsify the thesis

It should still avoid direct buy/sell conclusions by default.

## Phase 3: Structured Signal Layer

Goal: convert research conclusions into fields that can later be tested quantitatively.

### Principle

Do not send free-form long-form research into `QuantGPT`.

Instead, export a stable panel of structured observations by `date x ticker`, and where useful also by `date x segment`.

### Suggested first-generation feature set

Existing useful fields:

- `priority`
- `confidence`
- `should_flag_human_review`
- `evidence_count`
- `fresh_article_count`
- `structured_signal_count`
- `matches_theme`

New fields to add:

- `theme_id`
- `segment_id`
- `company_role`
- `chokepoint_score`
- `pricing_power_score`
- `qualification_barrier_score`
- `capacity_tightness_score`
- `customer_dependence_score`
- `value_capture_score`
- `substitution_risk_score`
- `route_change_risk_score`
- `evidence_freshness_days`
- `evidence_quality_score`

### Recommended storage seam

Introduce a stable export seam such as:

- Markdown report for humans
- JSON artifact for orchestration
- tabular export for quant validation

Possible tabular outputs:

- `reports/factor_validation/<run_id>/signals.parquet`
- `reports/factor_validation/<run_id>/signals.csv`

## Phase 4: Quant Validation With QuantGPT

Goal: test whether structured research signals behave like useful factors.

### What QuantGPT should do

- define exact factor formulas from the exported signals
- evaluate IC and RankIC
- run decile or quintile bucket tests
- compare short holding windows vs medium holding windows
- test lag sensitivity
- run industry-neutral and size-neutral variants
- compare against baseline signals such as momentum, revisions, or quality if available

### What QuantGPT should not do

- own the default news workflow
- invent narratives from scratch
- replace chokepoint mapping
- become mandatory for normal watchlist work

### Recommended invocation pattern

The normal path should remain:

`financial-agent -> chokepoint workflow -> optional QuantGPT validation`

This keeps the system robust even when `QuantGPT` is unavailable.

## Phase 5: Combined Research Loop

Goal: make the system iterative instead of one-shot.

### Closed loop

1. fresh news updates the theme or segment evidence
2. chokepoint scores or watch items update
3. company mappings update
4. factor panel refreshes
5. QuantGPT reruns validation on the changed signal population
6. monitoring triggers decide whether a thesis needs re-underwriting

### Result

Research and quant become linked, but not coupled into a single fragile tool.

## Suggested Tooling Evolution

## Near-term tools

Add these before advanced UI work:

- `run_theme_research`
- `read_theme_research_asset`
- `run_chokepoint_analysis`
- `read_chokepoint_summary`
- `export_factor_signal_panel`
- `run_quant_validation`

## Contract expectations

`run_theme_research` should return:

- theme summary
- supply-chain map summary
- candidate segments
- related companies
- artifact paths

`run_chokepoint_analysis` should return:

- thesis or theme scope
- six-criteria checks
- chokepoint scorecard
- failure paths
- artifact paths

`export_factor_signal_panel` should return:

- run id
- output path
- row count
- feature list
- date coverage

`run_quant_validation` should return:

- tested signal definitions
- validation window
- IC summary
- bucket return summary
- robustness notes
- report path

## Suggested Data Model Evolution

You do not need to implement everything at once, but these seams are likely worth introducing:

- `themes`
- `segments`
- `theme_segment_links`
- `company_segment_roles`
- `segment_evidence`
- `company_signal_snapshots`
- `segment_signal_snapshots`
- `quant_validation_runs`

The most important early choice is to keep `theme`, `segment`, and `ticker` distinct. Do not flatten them into one table too early.

## Recommended MVP Sequence

If implementation time is limited, do these in order:

1. add a `theme_research` workflow and asset layout
2. add `candidate chokepoint` scoring for segments
3. map segments to companies with explicit role labels
4. export per-date structured signals for each ticker
5. connect `QuantGPT` only for validation of those exported signals

This is the minimum path from “news research system” to “research system that can generate and test factor hypotheses”.

## Non-Goals For V1

Avoid these in the first pass:

- end-to-end auto-trading logic
- one giant blended score combining Buffett quality, chokepoint strength, and short-term news
- rich visualization dashboards before stable artifacts exist
- direct LLM-generated factor definitions with no structured intermediate layer
- making every ordinary research request depend on `QuantGPT`

## Decision Summary

The clean architecture is:

- `financial-agent` owns evidence, narrative, themes, segments, and ticker mapping
- `serenity-chokepoint-analysis` owns bottleneck framing and chokepoint discipline
- `QuantGPT` owns factor testing and quantitative validation

The clean execution order is:

1. theme and supply-chain foundation
2. chokepoint workflow
3. structured signal layer
4. quant validation
5. closed-loop refresh and monitoring

That order preserves clarity, avoids premature quant theater, and gives each subsystem a job it is well suited to do.
