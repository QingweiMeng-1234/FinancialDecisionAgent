# OpenClaw MVP Integration Plan

## 1. MVP Goal

OpenClaw acts as the orchestration layer.
`financial-agent` acts as the default research MCP.
`quantgpt` acts as an on-demand quantitative MCP.

The MVP acceptance path is intentionally narrow:

- Single-stock research can run end to end.
- Watchlist triage can run end to end.
- The default research path does not require `QuantGPT` to be running.
- `refresh_news` can run at most once per day.

## 2. MVP Architecture

The MVP integrates through MCP only. It does not open native OpenClaw plugins.

The orchestration layer assumes four working roles:

- `news-researcher`
- `watchlist-analyst`
- `factor-analyst`
- `reporter`

The default path stays in the research lane and only pulls in `quantgpt` when the task explicitly asks for quantitative validation.

## 3. Financial-Agent MCP Surface

The `financial-agent` MCP server exposes exactly six tools in the MVP:

- `refresh_news`
- `query_news_research`
- `recommend_stock`
- `run_watchlist_triage`
- `get_company_profile`
- `retrieve_supporting_articles`

`refresh_news` is the only write-capable research tool in the MVP surface.
All other tools read from existing indexed or persisted data.

## 4. Model Management

The orchestration default is a layered DeepSeek policy:

- default node model: `deepseek-v4-flash`
- critical node model: `deepseek-v4-pro`
- full-DeepSeek mode: supported when the caller wants a single-provider run

The OpenClaw orchestration layer also defaults to `flash`.
Critical decision points such as recommendation and watchlist prioritization can escalate to `pro`.

## 5. Routing Boundary

Default research tasks should stay on `financial-agent` only:

- single-stock research
- watchlist triage
- grounded evidence lookup
- company profile lookup

Only explicit quantitative validation should require `quantgpt`.
This keeps the default path stable even when `QuantGPT` is unavailable.

## 6. TDD Implementation Slices

The MVP implementation follows these vertical slices:

1. Add the `financial-agent` MCP surface and register the six fixed tools.
2. Keep the default research flow independent from `QuantGPT`.
3. Make `refresh_news` the only mutating research tool.
4. Persist refresh state and enforce one refresh per day.
5. Return structured outputs by default and only emit report paths when requested.
6. Reuse the entity KB and retrieval orchestration for profile and evidence tools.
7. Add OpenClaw-facing security defaults and allowlist helpers.
8. Add model selection helpers for `flash` versus `pro`.
9. Add route-policy helpers to keep default tasks on `financial-agent`.
10. Land this documentation as part of the implementation.

## 7. MVP Acceptance

The MVP is accepted when:

- `financial-agent` loads and exposes the six expected tools.
- `query_news_research`, `recommend_stock`, and `run_watchlist_triage` work without `QuantGPT`.
- `refresh_news` skips the second run on the same calendar day.
- profile and supporting-article tools return stable structured payloads.
- OpenClaw security defaults deny host execution and native plugin expansion.

## 8. Post-MVP Phases

### Phase 1.5: Retrieval Reinforcement

Goal:
Improve low-recall cases without changing the default MVP route.

Scope:

- ticker alias and company-name expansion
- richer `entity_kb` company context
- second-pass retrieval when first-pass evidence is weak
- retrieval change tracking tied into `retrieval_eval`

Non-goals:

- no mandatory auto-expansion in MVP acceptance
- no premature lock-in on complex heuristics

### Phase 2: Deeper QuantGPT Collaboration

Goal:
Join news research with factor validation in a deliberate combined workflow.

Scope:

- explicit quantitative-research tasks trigger `quantgpt`
- optional factor-validation follow-up after stock research
- `QuantGPT` availability checks before route escalation

Non-goals:

- no default auto-start of `QuantGPT`
- no requirement that every research task pass through quant validation

### Phase 3: Operational Automation

Goal:
Move from an interactive research tool toward a continuously running assistant.

Scope:

- scheduled `refresh_news`
- scheduled watchlist triage
- daily and weekly report aggregation
- long-running task status tracking
- retry and human-review handoff points

Non-goals:

- not part of MVP acceptance
- no heavy scheduling platform required at first launch

### Phase 4: Multi-Channel Distribution

Goal:
Deliver research outputs into external channels safely.

Scope:

- Telegram, Discord, and email distribution
- channel-specific message templates
- report links, evidence summaries, and priority cards

Non-goals:

- no outbound sending permissions in MVP
- no external channel access before allowlist and trust settings exist

### Phase 5: Stronger Security and Permissions

Goal:
Preserve a minimal attack surface as capabilities expand.

Scope:

- finer-grained tool classes
- permission isolation by channel, role, and task type
- approval flow and security audit hooks
- sender allowlist, pairing, and trust rules for external channels

Non-goals:

- no `host exec` in the MVP phase
- no rollback from allowlist-first defaults just to support later expansion
