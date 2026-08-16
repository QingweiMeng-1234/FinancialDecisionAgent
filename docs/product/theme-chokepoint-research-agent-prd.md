# Theme Chokepoint Research Agent PRD

## Document Control

| Field | Value |
| --- | --- |
| Status | Draft v0.6 aligned to v1.1 freeze candidate |
| Date | 2026-08-15 |
| Product | Financial Agent |
| Proposed capability | Theme Chokepoint Research Agent |
| Primary user | Human investment researcher |
| Default operating mode | Evidence-grounded research assistance |
| Scoring contract candidate | `docs/product/theme-chokepoint-scoring-contract-v1.1.zh-CN.md` |
| Historical scoring contract | `docs/product/theme-chokepoint-scoring-contract-v1.zh-CN.md` |
| Boundary-regression evidence | `docs/product/hbm-advanced-packaging-golden-regression-v1.1-2026-08-15.zh-CN.md`; not sufficient by itself to freeze weights or gates |

## 1. Executive Summary

The Theme Chokepoint Research Agent helps a human researcher turn a market hotspot or a user-defined theme into an auditable upstream supply-chain investigation.

The system starts from a theme, identifies the concrete demand shock and affected end products, expands their upstream dependencies, tests which segments may be genuine chokepoints, and maps those segments to companies that may own, expand, challenge, replace, or solve the constrained capacity.

The product does not make autonomous investment decisions. Its job is to produce a reviewable research object that answers:

1. What changed, and why may it create incremental demand?
2. Which end products and systems receive that demand?
3. How does demand transmit through the upstream supply chain?
4. Which segments may constrain downstream delivery?
5. What original-source evidence supports or contradicts each chokepoint condition?
6. Which incumbent companies are difficult to replace within the defined product, region, and time horizon?
7. Which challengers have moved from announcements to qualification, volume production, and material customer adoption?
8. Can segment scarcity or replacement progress transmit into company revenue and profit?
9. What evidence is still missing, and what would falsify the thesis?
10. Which signals should be monitored as the chokepoint strengthens, weakens, transfers, or disappears?

The system will be delivered in stages. The first useful version is assisted rather than fully autonomous: the user supplies a theme and confirms the demand/product framing before the system performs deeper research. Automatic hotspot detection and continuous monitoring are later stages built on the same research contracts.

## 2. Current State

The repository already contains a theme-first research foundation:

- open-web discovery through a search provider;
- article fetching and canonical article storage;
- local vector retrieval;
- basic source classification and evidence counts;
- theme-level Markdown research assets;
- CLI and tests for the current theme workflow.

The current workflow is useful for collecting theme material, but it does not yet provide:

- a persistent demand-to-product hypothesis;
- a structured supply-chain graph;
- claim-to-original-source evidence links;
- a multi-iteration gap-driven research loop;
- a chokepoint assessment with explicit unknown states;
- evidence-backed segment, company-defensibility, replacement-momentum, and earnings-transmission scorecards;
- a company-specific substitution assessment bounded by product, region, horizon, and as-of date;
- thesis falsification and counterfactual search;
- resumable run state or incremental monitoring.

This PRD defines those missing product capabilities. It does not require replacing the existing article store, content pipeline, search provider, or vector store.

## 3. Problem Statement

Market hotspots generate large amounts of repetitive narrative. A researcher can easily identify companies that are mentioned alongside a theme, but it is harder to determine:

- whether the hotspot creates a measurable demand change;
- which products actually receive that demand;
- where the supply chain is unable to respond;
- whether a perceived shortage is structural, cyclical, or temporary;
- whether a company has operational and financial exposure rather than narrative exposure;
- whether an incumbent is genuinely difficult to replace or merely the current market leader;
- whether a challenger has achieved qualification and productive scale rather than only announcing a product or capacity plan;
- whether the market thesis has already weakened or been invalidated.

Generic theme summaries and ticker mentions do not solve this problem. The product needs a causal, evidence-bound chain:

```text
Hotspot
→ Demand Shock
→ End Product/System
→ Upstream Dependency Graph
→ Chokepoint Hypothesis
→ Original-Source Evidence and Counter-Evidence
→ Incumbent Defensibility and Challenger Replacement Momentum
→ Earnings Transmission
→ Monitoring, Transfer Detection, and Falsification
```

## 4. Target User and Jobs to Be Done

### 4.1 Primary User

A human investment researcher who wants to decide where to spend deeper research time.

### 4.2 Primary Jobs

When a new theme becomes important, the researcher wants to:

- translate a broad narrative into a concrete demand mechanism;
- see the relevant supply chain without manually assembling every dependency;
- identify a small set of plausible chokepoints;
- verify those chokepoints against original sources;
- find companies with direct, indirect, expansion, or substitution exposure;
- understand the strongest counter-thesis;
- retain a reusable research history that can be refreshed later.

### 4.3 Secondary Users

- a watchlist triage workflow that consumes theme and company signals;
- an evaluation workflow that measures research usefulness;
- a future quantitative validation workflow that consumes structured, dated signals.

## 5. Product Goals

### 5.1 Goals

1. Convert a user-defined or automatically detected hotspot into a bounded research request.
2. Require an explicit one-sentence demand-expansion hypothesis before deep research.
3. Build a typed, inspectable supply-chain graph with a configurable depth and node budget.
4. Treat RAG as discovery and navigation; use original-source spans as final evidence.
5. Evaluate every candidate segment using the same chokepoint criteria.
6. Keep `theme`, `product/segment`, `claim/evidence`, and `company` as distinct research objects.
7. Separate technical bottleneck strength from company value capture.
8. Separate incumbent defensibility from challenger replacement momentum instead of ranking only current leaders.
9. Represent business scores as evidence-backed ranges with explicit coverage, quality, and freshness rather than false precision.
10. Search for counter-evidence and falsification conditions before confirming a candidate.
11. Persist every run so it can be audited, resumed, compared, and refreshed.
12. Help the user decide what deserves deeper research without presenting an autonomous buy/sell verdict.

### 5.2 Non-Goals

The initial product will not:

- autonomously trade, size positions, or issue final buy/sell instructions;
- claim that a chokepoint automatically produces excess stock returns;
- perform full valuation or portfolio construction;
- crawl the entire web without query, source, cost, and iteration limits;
- treat social-media attention as proof of industrial demand;
- treat a company mention as proof of revenue or earnings exposure;
- convert raw LLM prose directly into a quantitative factor;
- guarantee a complete or objectively correct supply-chain map;
- replace analyst approval for ambiguous product framing in the assisted MVP;
- integrate QuantGPT before stable dated research signals exist.

## 6. Product Principles

1. **Causal chain before company list.** The system must not jump from a theme directly to tickers.
2. **Original source before synthesis.** A summary can guide retrieval but cannot be the sole proof for a critical claim.
3. **Unknown is a first-class state.** Missing evidence is not equivalent to false, and model confidence is not evidence.
4. **Support and contradiction travel together.** A candidate without counter-search is incomplete.
5. **Technical importance and profit importance are separate.** A real bottleneck may not create an investable beneficiary.
6. **Leadership is not defensibility.** Current scale or market share does not by itself prove that a company is difficult to replace.
7. **Replacement is a maturity process.** Announcements and samples are weaker evidence than qualification, design wins, productive volume, material revenue, and sustained share gains.
8. **Scores are contextual ranges.** A company assessment is specific to a segment, product, region, horizon, and as-of date; unknown evidence produces a score range rather than fake precision.
9. **History is append-only at the research-run level.** Later runs may supersede earlier views but must not erase them.
10. **Budgets are deterministic.** Depth, nodes, sources, iterations, time, and model calls are bounded by the controller.
11. **Human judgment remains visible.** Product selection, overrides, and feedback are stored with the run.

## 7. Ubiquitous Language

| Term | Product Meaning |
| --- | --- |
| Hotspot | A user-submitted or system-detected topic that may deserve theme research. Hotness is not investment merit. |
| Theme | The normalized research subject, region, horizon, and scope. |
| Demand Shock | The hypothesized change in quantity, specification, adoption, regulation, or timing that affects a product. |
| Product Anchor | A concrete end product or system through which theme demand enters the supply chain. |
| Segment | A typed upstream node such as component, equipment, material, software/IP, infrastructure, or regulatory dependency. |
| Dependency Edge | A directional relationship explaining how a downstream node depends on an upstream node. |
| Claim | A testable statement about demand, capacity, substitutability, qualification, pricing, or company exposure. |
| Evidence Card | A structured record binding a claim to an original-source span and its provenance. |
| Chokepoint Candidate | A segment that passes the minimum criteria and evidence gates; not a final investment conclusion. |
| Company Role | The company's relationship to a segment, such as owner, expander, solver, buyer, substitute, or equipment enabler. |
| Company Assessment Scope | The tuple of company, segment, product, region, time horizon, and as-of date to which a company score applies. |
| Defensibility | Evidence that an incumbent is difficult to replace within the defined assessment scope. |
| Replacement Momentum | Evidence that a challenger or alternative route is progressing toward qualified, productive, material substitution. |
| Earnings Transmission | Evidence that segment scarcity, capacity expansion, or replacement progress can affect company revenue and profit. |
| Score Range | A confirmed minimum score plus the additional score that remains possible because material criteria are unknown or conflicted. |
| Evidence Coverage | The share of weighted criteria that have a supported `true` or `false` result rather than `unknown` or unresolved conflict. |
| Falsification Condition | An observable fact or threshold that would weaken or invalidate the thesis. |
| Monitoring Trigger | A dated signal that should cause a segment, claim, or company assessment to be refreshed. |

## 8. End-to-End User Journey

### 8.1 Assisted Theme Research

1. The user submits a theme, region, horizon, and research goal.
2. The system normalizes the theme and proposes a one-sentence demand shock.
3. The system proposes three to five product anchors.
4. The user confirms, removes, or edits the product anchors.
5. The system expands each confirmed product upstream within the configured budget.
6. The system generates candidate chokepoint hypotheses and targeted evidence questions.
7. The system retrieves candidate documents, opens the original text, and records evidence cards.
8. The system assesses each candidate and identifies evidence gaps.
9. The system performs additional targeted retrieval for material gaps.
10. The system maps supported segments to incumbent owners, capacity expanders, solvers, and substitute suppliers.
11. The system separately scores segment chokepoint strength, incumbent defensibility, challenger replacement momentum, and earnings transmission.
12. The critic searches for substitutes, qualification progress, new productive capacity, demand weakness, and other counter-evidence.
13. The system produces score ranges, evidence coverage, a company competition matrix, open questions, and monitoring triggers.
14. The user marks useful, incorrect, missing, or overstated outputs for later evaluation.

### 8.2 Continuous Refresh

1. New evidence enters the article store.
2. The system identifies which existing themes, nodes, claims, or companies the evidence may affect.
3. Only affected research objects are reopened.
4. The system recalculates only affected score dimensions and preserves the prior score inputs.
5. The system records whether a chokepoint is emerging, strengthening, weakening, transferring to an adjacent node, invalidated, or unchanged.
6. A new run snapshot is created and compared with the previous run.

## 9. Functional Requirements

### FR-01 Research Request

The system shall accept:

- theme label;
- trigger or reason for research;
- region;
- as-of date;
- research horizon;
- analysis goal;
- optional seed products and companies;
- operating mode;
- depth, node, source, iteration, time, and cost budgets.

The request shall be stored before external retrieval begins.

### FR-02 Theme and Demand Framing

The system shall produce:

- normalized theme name;
- scope and explicit exclusions;
- one-sentence demand-expansion hypothesis;
- measurable demand variables;
- time horizon;
- unresolved framing questions.

If the demand hypothesis cannot be stated clearly, the run shall enter `NEEDS_CLARIFICATION` and stop deeper expansion.

### FR-03 Product Anchor Selection

The system shall propose a bounded list of product anchors with:

- product name;
- buyer or user;
- demand variable;
- explanation linking the theme to the product;
- confidence;
- supporting and missing evidence.

In assisted mode, supply-chain expansion shall not begin until the user confirms at least one anchor.

### FR-04 Supply-Chain Graph

For every confirmed product, the system shall recursively propose upstream dependencies.

Each node shall have a stable ID, normalized name, type, depth, status, description, and aliases.

Each edge shall record:

- downstream and upstream node IDs;
- relationship type;
- demand-transmission explanation;
- criticality hypothesis;
- substitute hypothesis;
- confidence;
- supporting claim IDs.

Expansion shall stop when the configured depth/node budget is reached or when a node is non-critical, sufficiently commoditized, duplicated, outside scope, or unsupported.

### FR-05 Evidence Acquisition

The system shall support:

- existing local corpus retrieval;
- open-web search;
- full original-text fetch;
- source and date classification;
- content hashes and canonical URLs;
- deduplication across URLs and repeated stories.

Retrieval shall be driven by missing claim fields rather than only broad theme similarity.

### FR-06 Evidence Cards and Claim Ledger

Every material judgment shall be represented as a claim.

Every evidence card shall include:

- claim ID and article ID;
- exact quote;
- source title, publisher, URL, and source type;
- publication date and data-as-of date when available;
- page, section, or text offsets when available;
- content hash;
- `supports`, `contradicts`, or `context_only` stance;
- limitations;
- extraction model and prompt version.

The system shall visibly separate:

- source fact;
- normalized fact;
- model inference;
- analyst judgment;
- open question.

### FR-07 Chokepoint Assessment

Every candidate segment shall be evaluated using the normative `theme-chokepoint-scoring-v1.1` contract. The v1.1 dimensions and weights are:

| Dimension | Weight | Required interpretation |
| --- | ---: | --- |
| Demand Pressure | 15 | Incremental demand reaches this segment rather than only the broad theme. |
| Downstream Criticality | 20 | Missing supply can delay, degrade, or stop downstream delivery. |
| Effective Supply Concentration | 15 | Qualified, productive, available supply is concentrated; nominal supplier count is insufficient. |
| Qualification Barrier | 15 | New supply requires material customer, regulatory, or process qualification time. |
| Capacity Inelasticity | 15 | Qualified saleable output cannot expand quickly within the assessment horizon. |
| Substitute Weakness | 20 | Alternative products, suppliers, processes, or architectures are not yet adequate. |

Every dimension shall use a 0-to-4 anchored rating interval, `evidence_state`, and `bound_type`. The system shall calculate:

```text
dimension_points_min = weight × rating_min / 4
dimension_points_max = weight × rating_max / 4
score_min = sum(dimension_points_min)
score_max = sum(dimension_points_max)
presence_coverage
resolved_coverage
decision_coverage
```

The v1.1 `candidate_chokepoint` gate is:

```text
demand_pressure.rating_min >= 2
AND downstream_criticality.rating_min >= 2
AND score_min >= 65
AND decision_coverage >= 65%
AND demand/customer-side direct evidence exists
AND supply-side direct evidence exists
AND substitution/counter-evidence search is complete
AND neither mandatory dimension has unresolved conflict
```

`constraint_persistence` is derived as `relief_horizon`; it is not a weighted score. The former `4/6` rule is explanatory history only. Any change to weights, anchors, formulas, or gates requires a new scoring-contract version.

Each compound anchor shall be decomposed into explicit required, alternative, and exclusion predicates. A model shall not satisfy an anchor by joining incomplete predicates across companies, products, customers/platforms, regions, periods, denominators, or metric definitions. Condition coverage is diagnostic only and cannot proportionally satisfy an anchor or hard gate.

### FR-08 Company Role and Assessment Scope

The system shall map companies to supported segments with explicit roles:

- bottleneck owner;
- capacity expander;
- bottleneck solver;
- downstream buyer;
- substitute supplier;
- equipment enabler.

For each company, the system shall separately assess:

- narrative exposure;
- operational exposure;
- revenue exposure;
- earnings exposure;
- capacity position;
- expansion status;
- qualification advantage;
- pricing-power evidence;
- route-change and substitution risk;
- remaining evidence gaps.

The system shall not infer earnings exposure from a theme mention alone.

Every company assessment shall be bound to:

```text
company_id
segment_id
product_id
customer_or_platform_scope
geography
time_horizon
as_of_date
```

The system shall not label a company globally or permanently `irreplaceable` or `replaceable` without this scope.

### FR-08A Company Defensibility Score

For incumbents and current bottleneck owners, the system shall calculate a separate 100-point defensibility range using the v1.1 weights:

| Dimension | Weight | Required interpretation |
| --- | ---: | --- |
| Technical Performance Gap | 20 | Verified performance, power, yield, reliability, or process advantage over qualified alternatives. |
| Qualification Lock-in | 15 | Time and difficulty required for customers to qualify another supplier or route. |
| Switching Cost | 15 | Redesign, migration, equipment, downtime, warranty, or operational risk imposed by switching. |
| Qualified Effective Capacity | 20 | Share and scale of qualified, productive, available capacity rather than announced nameplate capacity. |
| Quality and Delivery Reliability | 15 | Evidence of stable yield, defect performance, and on-time delivery at scale. |
| Customer Sourcing Evidence | 15 | Customer-side sole-source, dual-source, dependency, renewal, or switching behavior. |

Market leadership, company size, patent count, and management moat language shall not by themselves establish defensibility.

### FR-08B Replacement Momentum Score

For challengers, substitute suppliers, and alternative routes, the system shall calculate a separate 100-point replacement-momentum range using the v1.1 weights:

| Dimension | Weight | Required interpretation |
| --- | ---: | --- |
| Performance Parity | 20 | The alternative meets target-product requirements; this is a non-compensable gate. |
| Qualification Progress | 15 | Progress from sample to customer validation, design selection, qualification, and production approval. |
| Capacity Readiness | 15 | Funded, installed, qualified, ramped, saleable output rather than announced future capacity. |
| Customer Adoption | 15 | Paid deployment, production use, repeat delivery, and multi-platform adoption; excludes qualification, revenue, and share. |
| Cost or TCO Advantage | 15 | Verified complete TCO after integration, migration, yield, downtime, and operating costs. |
| Execution and Delivery | 10 | Ability to fund, build, qualify, ramp, and deliver consistently. |
| Regulatory Tailwind | 5 | Policy change that materially lowers replacement barriers. |
| Architecture Tailwind | 5 | Architecture change that materially lowers replacement barriers. |

Ecosystem Compatibility is a non-weighted hard gate. The system shall preserve independent milestone axes rather than a single maturity ladder:

```text
product_readiness
qualification
production
adoption
financial
share_trajectory
displacement
```

An announcement, prototype, or sample shall not be treated as completed replacement.

`realized_replacement` additionally requires supported rating floors of at least 3 for Performance, Qualification, Capacity, Adoption, Ecosystem, and Displacement in the same current Scope. Revenue, market presence, share growth, or the weighted total cannot compensate for any missing gate.

### FR-08C Earnings Transmission Score

The system shall calculate a separate 100-point earnings-transmission range using the v1.1 weights:

| Dimension | Weight | Required interpretation |
| --- | ---: | --- |
| Revenue Materiality | 20 | Product revenue is material to the issuer or stable reportable segment; market share is separate. |
| Volume Realization Leverage | 15 | The company has qualified saleable capacity to convert demand into shipments and operating leverage. |
| Pricing Power | 15 | Price, contract, mix, or allocation evidence shows value capture rather than demand alone. |
| Margin Transmission | 20 | Incremental revenue improves gross or operating profit after relevant costs. |
| Time to Revenue | 10 | Capacity, qualification, shipment, and revenue-recognition timing fit the research horizon. |
| Capital and Cash Burden | 10 | Required capital, depreciation, working capital, and ramp costs do not absorb the expected benefit. |
| Customer Concentration Risk | 5 | Customer bargaining power does not eliminate expected value capture. |
| Earnings Persistence | 5 | Revenue and margin effects are not solely a short-lived inventory or pricing event. |

Earnings transmission shall follow the explicit chain:

```text
demand
→ order or committed customer adoption
→ qualified saleable capacity
→ shipment
→ revenue recognition
→ price/mix or volume benefit
→ incremental profit after capital and operating costs
```

### FR-08D Company Competition State

The product shall show defensibility and replacement momentum as separate axes, with earnings transmission as a separate filter.

The high-level company-state rules are:

| Defensibility and challenger evidence | State |
| --- | --- |
| Any supported actual displacement | `vulnerable_incumbent` |
| Low + replacement-ready challenger | `vulnerable_incumbent` |
| High/Medium + credible challenger | `contested_chokepoint_owner` |
| High + frozen challenger set contains no credible challenger | `durable_chokepoint_owner` |
| Medium + no credible challenger | `differentiated_incumbent` |
| Low + no ready challenger | `commodity_or_weakly_differentiated` |

Potential challengers receive cumulative gates and one primary state from credible, ready, production, scaled, and realized replacement states.

Competition state requires a frozen, reproducible `challenger_set`; open-world absence cannot establish durability. Actual displacement cannot be compensated by an incumbent's historical defensibility score.

```text
business_state:
  eligible: true
  achieved_gates: []
  primary_state: contested_chokepoint_owner
```

The complete threshold, multi-axis milestone, evidence, interval, Coverage, and ambiguity semantics are normative only in `theme-chokepoint-scoring-v1.1`.

### FR-09 Counterfactual and Falsification Review

Before a candidate can be reported as supported, the system shall search for:

- demand weakness or order pull-forward;
- inventory accumulation;
- alternative products, suppliers, processes, or architectures;
- announced and effective capacity additions;
- shorter-than-expected qualification or switching cycles;
- customer dual-sourcing, redesign, insourcing, or successful supplier switching;
- challenger failure to qualify, ramp yield, build productive capacity, or win sustained volume;
- regulatory or route changes;
- inability to translate scarcity into price, revenue, or margin.

The output shall contain explicit falsification conditions and unresolved counterarguments for the segment chokepoint, incumbent defensibility, challenger replacement momentum, and earnings-transmission theses separately.

### FR-10 Gap-Driven Loop

After each assessment, the controller shall identify missing material fields and generate targeted queries.

The loop shall stop when:

- all required gates pass;
- no unresolved gap can change the candidate decision;
- the configured iteration/source/time/cost budget is exhausted;
- human clarification is required;
- retrieval repeatedly fails for a required evidence class.

Budget exhaustion shall return an incomplete terminal state rather than a forced conclusion.

### FR-11 Persistence, Resume, and Reproducibility

The system shall persist:

- run state and stage;
- request and budgets;
- nodes and edges;
- questions and queries;
- retrieved source IDs;
- claims and evidence cards;
- assessment results;
- company mappings;
- score dimensions, weights, ranges, coverage, quality, freshness, and scoring version;
- capacity and replacement maturity milestones;
- user decisions;
- model, prompt, parser, and scoring versions.

A run interrupted after a committed stage shall resume from stored state without repeating completed side effects.

### FR-12 Research Artifacts

Each run shall generate:

```text
run.json
theme-card.md
supply-chain-nodes.jsonl
supply-chain-edges.jsonl
supply-chain-map.md
claim-ledger.jsonl
chokepoint-scorecard.md
company-exposure.md
company-competition-scorecard.md
counter-evidence.md
monitoring-triggers.md
```

Run-specific artifacts shall be immutable. A latest summary may be regenerated from the latest completed run.

### FR-13 Human Feedback

The user shall be able to record:

- incorrect product anchor;
- missing or incorrect supply-chain edge;
- unsupported claim;
- wrong source interpretation;
- missed counter-evidence;
- incorrect company role;
- incorrect defensibility, replacement-maturity, earnings-transmission, or company-state assessment;
- correction to a dimension's `evidence_state`, `bound_type`, rating interval, predicate state, or business-state Gate;
- useful or not useful candidate;
- free-text correction.

The legacy `true/false/unknown/conflicted` feedback shape is not valid for v1.1 score dimensions and shall not be emitted. Feedback shall reference stable run and object IDs.

### FR-14 Automatic Hotspot Detection

In a later stage, the system shall cluster recent structured events and propose hotspot candidates based on:

- volume acceleration;
- source diversity;
- novelty relative to existing themes;
- company and sector breadth;
- primary-source confirmation;
- persistence across time windows.

Hotspot scores shall represent research priority only. They shall not be reused as chokepoint or investment scores.

### FR-15 Incremental Monitoring

The system shall allow monitoring triggers to target a theme, node, claim, or company.

Examples include:

- capacity-announcement and production-start dates;
- lead-time changes;
- price changes;
- customer qualification;
- design wins, pilot volume, mass production, and material revenue;
- order cancellation;
- substitute adoption;
- demand-volume or unit-content changes;
- changes in qualified supplier count, yield, delivery reliability, or customer sourcing behavior.

New evidence shall update the affected claim and score dimension rather than directly overwriting the overall conclusion.

New runs shall report state changes as `emerging`, `strengthening`, `weakening`, `transferring`, `invalidated`, or `unchanged`, with the evidence responsible for the change.

`transferring` shall require evidence that the former constraint is easing, an adjacent upstream or downstream node is strengthening as a constraint, and the underlying demand has not simply disappeared.

## 10. Core Data Contracts

### 10.1 Research Request

```json
{
  "theme": "AI data-center power infrastructure",
  "trigger": "AI compute deployment growth",
  "region": "global",
  "as_of_date": "2026-08-15",
  "time_horizon_months": 24,
  "analysis_goal": "identify upstream chokepoints and potential beneficiaries",
  "seed_products": [],
  "research_mode": "assisted",
  "max_depth": 3,
  "max_nodes": 30,
  "max_iterations": 3,
  "max_sources": 40
}
```

### 10.2 Dependency Edge

```json
{
  "edge_id": "edge_001",
  "downstream_node_id": "product_ai_server",
  "upstream_node_id": "segment_hbm",
  "relation_type": "requires_component",
  "demand_transmission": "More accelerators and higher memory per accelerator increase HBM demand.",
  "criticality": "high",
  "substitutability": "unknown",
  "confidence": 0.68,
  "claim_ids": ["claim_014", "claim_018"]
}
```

### 10.3 Evidence Card

```json
{
  "evidence_id": "ev_001",
  "claim_id": "claim_014",
  "origin_event_id": "event_earnings_call_2026_q2",
  "evidence_family_id": "family_company_q2_disclosure",
  "article_id": 123,
  "content_sha256": "...",
  "source_type": "company_filing",
  "publication_date": "2026-07-30",
  "data_as_of": "2026-Q2",
  "exact_quote": "Original source text...",
  "location": {"page": 12, "section": "Capacity"},
  "stance": "supports",
  "fact_key": "qualified_capacity_timing",
  "primary_scoring_dimension": "capacity_readiness",
  "condition_ids": ["condition_capacity_output_nonzero"],
  "evidence_state": "supported",
  "bound_type": "lower_bound",
  "rating_min": 2,
  "rating_max": 4,
  "limitations": ["Company-reported", "Future capacity is not qualified output"]
}
```

### 10.4 Chokepoint Assessment

```json
{
  "node_id": "segment_hbm",
  "contract_id": "theme-chokepoint-scoring-v1.1",
  "score_min": 67.5,
  "score_max": 82.5,
  "score_width": 15.0,
  "coverage": {"presence": 0.9, "resolved": 0.8, "decision": 0.7},
  "counter_search_completed": true,
  "business_state": {
    "eligible": true,
    "achieved_gates": ["watch_segment", "candidate_chokepoint"],
    "primary_state": "candidate_chokepoint"
  },
  "evidence_quality": "medium",
  "falsification_conditions": [
    "Qualified supply grows faster than expected",
    "Unit memory demand falls because of architecture changes"
  ]
}
```

### 10.5 Evidence-Backed Score

```json
{
  "score_type": "company_defensibility",
  "assessment_scope": {
    "company_id": "company_001",
    "segment_id": "segment_001",
    "product_id": "product_001",
    "customer_or_platform_scope": "target_platform_001",
    "geography": "global",
    "time_horizon_months": 12,
    "as_of_date": "2026-08-15"
  },
  "dimensions": [
    {
      "name": "qualification_lock_in",
      "weight": 15,
      "rating_min": 3,
      "rating_max": 4,
      "evidence_state": "supported",
      "bound_type": "lower_bound",
      "primary_claim_ids": ["claim_101"],
      "floor_only_claim_ids": ["claim_102"],
      "condition_coverage": {
        "required_condition_count": 3,
        "resolved_required_condition_count": 2
      }
    },
    {
      "name": "customer_sourcing_evidence",
      "weight": 15,
      "rating_min": 0,
      "rating_max": 4,
      "evidence_state": "unknown",
      "bound_type": "none",
      "primary_claim_ids": []
    }
  ],
  "score_min": 65,
  "score_max": 75,
  "score_width": 10,
  "coverage": {
    "presence": 0.9,
    "resolved": 0.8,
    "decision": 0.7
  },
  "business_state": {
    "eligible": true,
    "achieved_gates": [],
    "primary_state": "medium_defensibility"
  },
  "contract_id": "theme-chokepoint-scoring-v1.1"
}
```

`confidence` shall not replace the score range or evidence coverage. A model may be confident while the evidence remains incomplete.

### 10.6 Replacement Milestone

```json
{
  "company_id": "challenger_001",
  "segment_id": "segment_001",
  "milestones": {
    "product_readiness": "sample_ready",
    "qualification": "testing",
    "production": null,
    "adoption": "evaluation",
    "financial": null,
    "share_trajectory": null,
    "displacement": null
  },
  "milestone_evidence_state": {
    "qualification": "supported",
    "production": "unknown"
  },
  "claim_ids": ["claim_201"],
  "next_required_gate": "production_approved"
}
```

### 10.7 Company Competition Assessment

```json
{
  "contract_id": "theme-chokepoint-scoring-v1.1",
  "assessment_scope_id": "scope_001",
  "incumbent_company_id": "company_001",
  "challenger_company_ids": ["challenger_001"],
  "defensibility_score_range": [65, 75],
  "replacement_momentum_score_range": [35, 60],
  "earnings_transmission_score_range": [50, 70],
  "challenger_set_id": "challenger_set_001",
  "business_state": {
    "eligible": true,
    "achieved_gates": ["credible_challenger_present"],
    "primary_state": "contested_chokepoint_owner"
  },
  "unresolved_material_questions": [
    "Has the challenger completed production qualification?",
    "What share of the incumbent segment revenue is exposed within 12 months?"
  ]
}
```

## 11. Evidence and Source Policy

### 11.1 Source Priority

1. regulatory filing, government data, and official policy;
2. company filing, investor presentation, and earnings-call transcript;
3. customer and supplier cross-confirmation;
4. industry association and high-quality specialist data;
5. reputable news reporting;
6. commentary, social media, and search-trend signals.

Lower-tier sources may trigger research but shall not alone confirm a material chokepoint condition.

Source independence shall be evaluated by `origin_event_id` and `evidence_family_id`, not URL or publisher count alone. Derivative reporting of the same management statement counts as one evidence family for corroboration. Multiple independent facts in one original source may be split into distinct `fact_key` values, but do not become independent sources.

### 11.2 Time Semantics

The system shall distinguish:

- publication date;
- data-as-of date;
- retrieval date;
- research-run as-of date;
- expected future milestone date.

### 11.3 Evidence Sufficiency

Evidence sufficiency shall be assessed per material claim, not only by document count.

The system shall report:

- required claims;
- claims with support;
- claims with contradiction;
- claims with only contextual evidence;
- claims with no evidence;
- freshness and source-quality gaps.

### 11.4 Two-Sided Original-Source Verification

The system shall define required claims before searching for evidence. A segment or company score shall not be generated first and justified afterward with convenient quotations.

For material chokepoint and defensibility conclusions, the system should seek at least:

```text
one supply-side source
+ one customer or demand-side source
+ one substitution or counter-evidence search
```

Source roles shall remain explicit:

| Source role | Stronger uses | Insufficient alone for |
| --- | --- | --- |
| Upstream supplier | Orders, current output, expansion, yield, pricing, delivery | Customer inability to switch |
| Downstream customer | Dependency, qualification, sourcing, delivery impact, demand | Total upstream effective capacity |
| Challenger | Product roadmap, samples, targeted capacity | Completed qualification or material substitution |
| Filing or regulator | Revenue, segment exposure, risks, customer concentration | Latest operating change not yet disclosed |
| Independent industry data | Share, price, capacity, lead time, cross-company comparison | A specific customer's completed qualification |

The minimum claim-requirement matrix is:

| Decision field | Required claims | Preferred original evidence | Required counter-search |
| --- | --- | --- | --- |
| Demand transmission | End demand, unit content, adoption or specification change reaches the segment | Customer capex/procurement, shipment data, product specifications, buyer filing or call | Inventory build, order pull-forward, double ordering, demand reduction |
| Downstream criticality | Missing supply delays, degrades, or stops downstream delivery | Customer delay disclosure, engineering requirement, production interruption, qualification rule | Workaround, specification reduction, inventory buffer, redesign |
| Effective concentration | Few suppliers have qualified productive available output | Approved vendor information, customer sourcing disclosure, productive capacity/yield data | Additional qualified suppliers, hidden available capacity, regional alternatives |
| Capacity inelasticity | Saleable output cannot expand within the horizon | Construction, equipment, qualification, yield-ramp, and output milestones | Funded capacity, faster qualification, improved yield, demand cancellation |
| Incumbent defensibility | Customer cannot switch without material technical, time, cost, or operational impact | Customer sourcing behavior, qualification records, technical comparison, migration cost | Dual sourcing, successful redesign, switching, price concessions, share loss |
| Replacement momentum | Alternative is progressing toward qualified productive material adoption | Customer validation, design win, qualification, shipment, material revenue, share data | Failed validation, delayed ramp, yield problems, lost design, insufficient capacity |
| Earnings transmission | Scarcity or replacement progress can affect revenue and profit within the horizon | Segment revenue, backlog conversion, ASP/mix, gross margin, capacity output, capex/depreciation | Fixed pricing, customer bargaining, cost inflation, dilution, delayed recognition |

A supplier's statement that its product is critical or differentiated shall remain self-reported evidence until corroborated by customer behavior, technical requirements, or observed market outcomes.

### 11.5 Capacity and Replacement Maturity Semantics

Capacity shall be tracked through:

```text
announced
→ funded
→ under construction
→ equipment installed
→ qualification
→ yield ramp
→ stable productive output
→ saleable output
```

Replacement shall be tracked on independent axes:

```text
product_readiness: concept | prototype | sample_ready
qualification: not_started | testing | design_selected | qualified | production_approved
production: none | pilot | ramp | mass_production | stable_scaled_output
adoption: evaluation | paid_pilot | production_use | recurring_use | multi_platform_use
financial: no_revenue | realized_unquantified | single_period_material | material_revenue | recurring_material_revenue
share_trajectory: losing | stable | single_period_gain | sustained_gain
displacement: none | partial | confirmed
```

Axes shall not be collapsed into a single maturity ordinal. Production does not imply revenue; revenue does not imply share gain; share gain does not imply displacement.

## 12. Research Controller and Agent Boundaries

### 12.1 Deterministic Controller

The controller owns:

- state transitions;
- budgets and iteration limits;
- schema validation;
- persistence boundaries;
- ready-work selection;
- retries for safe read-only operations;
- terminal-state decisions;
- human approval gates.

### 12.2 Planner

The planner owns theme normalization, demand framing, measurable variables, and product-anchor proposals. It does not confirm chokepoints or companies.

### 12.3 Graph Builder

The graph builder proposes upstream nodes and edges. It does not treat proposed edges as verified facts.

### 12.4 Evidence Reader

The evidence reader converts original text into evidence cards. It does not decide whether a company is attractive.

### 12.5 Chokepoint Evaluator

The evaluator applies explicit criteria using claim and evidence state, calculates the segment score range and evidence coverage, and preserves conflicts. It cannot convert `unknown` into `true` based on intuition.

### 12.6 Company Mapper

The company mapper records role, assessment scope, exposure, capacity, and value-capture hypotheses. It must keep narrative, operational, revenue, and earnings exposure separate.

### 12.7 Company Competition Evaluator

The company competition evaluator separately calculates defensibility, replacement momentum, and earnings-transmission ranges. It owns neither source discovery nor the final investment decision.

### 12.8 Critic

The critic owns counterfactual search, reasoning-jump checks, falsification conditions, and unresolved-risk reporting.

## 13. Non-Functional Requirements

### 13.1 Auditability

Every material conclusion must be traceable to stable claims, evidence cards, and original content hashes.

### 13.2 Reproducibility

Given the same code, prompt versions, request, and stored source snapshot, the system shall be able to reconstruct the persisted assessment. Exact natural-language wording need not be identical unless model determinism is configured.

### 13.3 Resumability

A process interruption shall not require restarting completed stages. External retrieval shall not execute inside a long-lived database transaction.

### 13.4 Cost and Scope Control

Every run shall expose and enforce configurable limits for:

- graph depth;
- node count;
- search queries;
- source count;
- original-text size;
- model calls;
- loop iterations;
- elapsed time.

Initial numeric defaults are planning inputs, not validated production SLOs.

### 13.5 Security

Fetched content shall be treated as untrusted data. Instructions embedded in source documents shall not modify Agent policy, tool permissions, research scope, or output contracts.

### 13.6 Explainability

The UI and reports shall show criterion-level results, weights, evidence, score minimum, score maximum, score width, evidence coverage, source quality, and freshness. A single opaque combined score is insufficient.

## 14. Success Metrics

The product shall initially optimize research usefulness, not investment returns.

### 14.1 Contract and Evidence Metrics

- percentage of material claims with original-source spans;
- percentage of candidate criteria marked `unknown` rather than unsupported inference;
- percentage of reported candidates with completed counter-search;
- citation correctness rate under human review;
- graph-node and edge correction rate;
- company-role correction rate;
- percentage of score dimensions with explicit source-side and customer-side evidence where required;
- percentage of replacement claims whose maturity stage is correctly supported;
- rate at which announcements are incorrectly treated as productive capacity or completed replacement.
- inter-annotator agreement on dimension intervals, hard gates, and final states;
- status confusion matrices and adjudicated false-positive/false-negative counts;
- pairwise ranking reversals and Top-10 human-review recall across Golden themes.

### 14.2 Research Utility Metrics

- percentage of top candidates judged worth deeper research;
- rate of false or overstated chokepoint candidates;
- rate of current leaders incorrectly labeled durable without defensibility evidence;
- rate of challengers incorrectly promoted before qualification or productive scale;
- analyst correction rate for company competition state;
- analyst time required to reach a reviewable shortlist;
- number of material missed segments found by the analyst;
- percentage of monitoring updates judged meaningfully new;
- repeatability of outputs from stored evidence snapshots.

### 14.3 Later Quantitative Metrics

Information coefficient, bucket returns, and robustness tests belong to a later quantitative validation stage. They shall not be used as MVP completion criteria.

## 15. Delivery Stages

### Stage 0: Contract and Golden Case

**Goal:** Freeze product semantics before implementing runtime behavior.

**Scope:**

- approve this PRD;
- select one concrete Golden Case;
- manually define expected demand hypothesis, products, key supply-chain nodes, plausible chokepoints, incumbent and challenger cases, counterexamples, and evidence;
- annotate positive and negative assertions for durable, contested, vulnerable, false-leader, immature-challenger, and weak-earnings states; do not fabricate positive labels where public evidence is insufficient;
- freeze the first version of node, edge, claim, evidence, assessment, company-role, score-range, and replacement-maturity schemas;
- evaluate the scoring-contract candidate against the Golden Case, resolve semantic defects, then freeze the passing version;
- establish a reproducible test environment and baseline current tests.

**Exit criteria:**

- one approved Golden Case;
- approved schemas and state semantics;
- the v1.1 candidate has passed all HBM boundary assertions plus cross-theme positive-case, double-annotation, adjudication, and calibration Gates before being relabeled frozen;
- explicit MVP source and budget policy;
- current Theme tests can be executed in a controlled environment.

### Stage 1: Assisted Theme and Product Framing

**Goal:** Turn a user theme into a confirmed, bounded research object.

**Scope:** FR-01 through FR-03 and persistent run state.

**Exit criteria:**

- the system produces a reviewable demand hypothesis and product list;
- ambiguous requests stop for clarification;
- user confirmation is persisted;
- no supply-chain expansion occurs before the assisted gate.

### Stage 2: Supply-Chain Graph

**Goal:** Build a bounded, typed, inspectable upstream dependency graph.

**Scope:** FR-04 plus graph artifacts and deduplication.

**Exit criteria:**

- maximum depth and node budgets are enforced;
- aliases do not create duplicate canonical nodes;
- every edge contains a transmission hypothesis and verification questions;
- proposed and supported edges remain distinguishable.

### Stage 3: Evidence and Chokepoint Loop

**Goal:** Determine which candidate segments have evidence for genuine constraint conditions.

**Scope:** FR-05 through FR-07 and FR-10.

**Exit criteria:**

- material claims bind to original-source spans;
- unsupported conditions remain `unknown`;
- the controller performs at least one gap-driven iteration when a material field is missing;
- budget exhaustion returns an explicit incomplete outcome;
- criterion-level scorecards, segment score ranges, and evidence coverage are generated.

### Stage 4: Company Exposure and Red Team

**Goal:** Identify who may defend, capture, challenge, replace, or remove the bottleneck and test the thesis against alternatives.

**Scope:** FR-08 and FR-09.

**Exit criteria:**

- company roles are explicit;
- narrative, operational, revenue, and earnings exposure are separated;
- company assessments are scoped by segment, product, region, horizon, and as-of date;
- defensibility, replacement momentum, and earnings transmission are reported as separate ranges;
- replacement maturity distinguishes announcements and samples from qualification, production, material revenue, and sustained share gain;
- company competition state is derived from visible gates rather than current leadership alone;
- every supported candidate has counter-evidence search and falsification conditions;
- no default buy/sell output is generated.

### Stage 5: Persistent Research Product

**Goal:** Make the workflow resumable, reviewable, and usable from Financial Agent interfaces.

**Scope:** FR-11 through FR-13, versioned artifacts, CLI, and MCP interfaces.

**Exit criteria:**

- interrupted runs resume from committed state;
- all output objects have stable IDs;
- users can submit corrections tied to exact objects;
- a later run can be compared with an earlier run.

### Stage 6: Automatic Hotspots and Monitoring

**Goal:** Propose new themes and refresh existing ones without flattening hotness into investment merit.

**Scope:** FR-14 and FR-15.

**Exit criteria:**

- automatically proposed hotspots show their trigger events and scoring inputs;
- users can accept or reject hotspot candidates;
- new evidence refreshes only affected research objects where possible;
- the system explains which claims and score dimensions changed;
- the system distinguishes strengthening, weakening, invalidation, and transfer to an adjacent node.

### Stage 7: Signal Export and Quantitative Validation

**Goal:** Export stable, dated research signals for independent testing.

**Scope:**

- dated segment and company snapshots;
- explicit signal definitions;
- CSV/Parquet export;
- optional QuantGPT validation.

**Exit criteria:**

- every exported signal has a documented formula and source lineage;
- no raw prose is treated as a factor;
- research evidence and quantitative validation results remain separate.

## 16. MVP Definition

The MVP consists of Stages 0 through 4 for one user-defined theme in assisted mode.

Default planning limits:

```text
1 theme per run
1-3 confirmed products
maximum graph depth: 3
maximum graph nodes: 30
maximum candidate chokepoints: 10
maximum research iterations: 3
maximum sources: 40
```

These are cost-control assumptions to validate during the Golden Case, not production capacity claims.

The MVP is successful when it can produce:

- a confirmed demand hypothesis;
- a reviewable three-level supply-chain graph;
- two to four evidence-backed chokepoint candidates or an honest insufficient-evidence result;
- original-source evidence and counter-evidence;
- segment chokepoint score ranges and evidence coverage;
- explicit company roles, assessment scope, and exposure gaps;
- separate defensibility, replacement-momentum, and earnings-transmission ranges;
- company competition states that do not equate market leadership with durability;
- replacement maturity that does not equate announcements with productive substitution;
- falsification conditions and monitoring triggers;
- no unsupported autonomous investment verdict.

## 17. Golden Cases

The approved normative boundary-regression case for `theme-chokepoint-scoring-v1.1` is:

```text
Theme: AI accelerators → HBM → advanced packaging
Region: Global; customer and platform scopes remain separate
As-of: 2026-08-15
Research question:
Can original-source evidence distinguish production, adoption, material revenue,
sustained share gain, qualified alternatives, and realized displacement without
mixing HBM stack supply with 2.5D integration routes?
```

This case is normative because it includes:

- Samsung HBM4 mass-production and production-use evidence without product revenue, qualification-scope, performance-comparison, share, or displacement proof;
- Micron HBM4 single-period issuer materiality without sustained-share or displacement proof;
- SK hynix mass-production-readiness language that cannot prove production occurred;
- Amkor HDFO production evidence that cannot migrate across product/customer Scope into a CoWoS replacement claim;
- an ambiguous transcript sentence that must remain `context_only` and cannot lift Qualification;
- synthetic hard-gate and state-priority counterexamples needed to test non-compensability.

The versioned boundary-regression input and result are:

- `hbm-advanced-packaging-golden-claim-set-v0.1.zh-CN.md` plus the normative `v0.2` revision layer;
- `hbm-advanced-packaging-golden-regression-v1.1-2026-08-15.zh-CN.md`.

Public evidence does not currently support a positive `durable_chokepoint_owner` Gold label in this case. That absence is intentional: the regression must reject unsupported durability rather than fabricate a positive example.

The required cross-theme validation candidate is AI data-center power and cooling infrastructure, with global and regional power, interconnection, transformer/switchgear, backup-power, and cooling constraints kept as separate scopes. Until this validation and double annotation are complete, v1.1 remains a freeze candidate.

## 18. Risks and Mitigations

| Risk | Mitigation |
| --- | --- |
| Theme is too broad | Require a one-sentence demand shock, region, horizon, and product confirmation. |
| Graph expands without limit | Enforce deterministic depth, node, query, source, and iteration budgets. |
| RAG chunks lose context | Retrieve by chunk but verify against parent text and save exact source spans. |
| Model invents supply-chain edges | Keep proposed and supported states separate; require edge claims and evidence. |
| Confirmation bias | Require critic queries and falsification conditions before confirmation. |
| Company name is mistaken for exposure | Require explicit company role and exposure-layer evidence. |
| Current leadership is mistaken for durability | Require scoped defensibility evidence, including customer sourcing and qualified alternatives. |
| Challenger announcements are mistaken for replacement | Preserve replacement maturity and require qualification, productive capacity, and customer adoption gates. |
| New capacity is treated as available capacity | Track announced, under construction, qualified, ramping, and productive states separately. |
| Research becomes stale | Store as-of semantics and monitoring triggers; compare versioned runs. |
| Scores appear more precise than evidence | Report confirmed-to-possible ranges, evidence coverage, quality, and freshness; avoid opaque blended scores. |
| One score hides different business questions | Keep segment chokepoint, defensibility, replacement momentum, and earnings transmission separate. |
| Source content contains prompt injection | Treat fetched text as untrusted evidence and isolate it from system/tool instructions. |
| Quant testing legitimizes weak research | Delay factor export until data contracts and evidence lineage are stable. |

## 19. Dependencies

The project depends on:

- stable article identity, canonical URL, content hash, and original text;
- reliable source fetching and validation;
- local retrieval and open-web search interfaces;
- company/entity normalization;
- model structured-output validation;
- versioned prompts and schemas;
- deterministic test doubles for search, fetching, retrieval, and model output.

Live-source integration tests are useful but shall not be required for normal unit-test execution.

## 20. Open Product Decisions

The v1.1 scoring weights, anchors, interval semantics, Coverage formulas, candidate gates, multi-axis milestones, relief model, and state priorities are the current design candidate. They cannot become an implementation baseline until the contract's re-freeze Gates pass. After a future freeze, semantic changes require a new scoring-contract version.

The following non-scoring decisions must still be resolved during Stage 0:

1. Is the default theme unit global, regional, or user-selected per run?
2. Should one run permit multiple product anchors, or should each product become a child run?
3. Should user confirmation be mandatory only for product anchors or also for initial graph structure?
4. How should private research documents be permissioned and cited?
5. Which company universe is supported first: U.S.-listed, global public, or public and private?
6. Should valuation remain entirely outside this product or appear as an optional final research field?
7. Which monitoring cadence is useful: daily, weekly, event-driven, or user-triggered?
8. What additional cross-theme calibration set should follow the approved HBM/advanced-packaging Golden and AI-data-center power/cooling validation candidates?

## 21. Approval Gates

The HBM/advanced-packaging input is versioned and its boundary regression has passed, but the scoring contract is not frozen. Before runtime implementation begins, reviewers must approve:

- product problem and primary user;
- MVP boundary;
- assisted-mode user journey;
- ubiquitous language;
- stage order and dependencies;
- predicate-level claim/evidence semantics and evidence-family deduplication;
- the chokepoint candidate gate after cross-theme calibration;
- score-range and evidence-coverage semantics after double annotation;
- company exposure layers and assessment scope;
- company defensibility, replacement-momentum, and earnings-transmission dimensions;
- replacement and capacity maturity semantics;
- the numerical acceptance thresholds for annotator agreement, state errors, pairwise ordering, and Top-10 recall;
- `theme-chokepoint-scoring-v1.1` as the frozen implementation baseline only after every re-freeze Gate passes;
- required Golden Case labels;
- HBM/advanced-packaging as the normative Golden boundary case and AI data-center power/cooling as the next validation candidate;
- unresolved decisions that must be answered before Stage 1.

Approval of this PRD authorizes design and implementation planning. It does not by itself authorize automated trading, production deployment, live paid-source access, or quantitative claims.
