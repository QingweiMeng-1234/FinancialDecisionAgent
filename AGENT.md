# AGENT.md

## Project: Financial Watchlist Triage System

This project is a lightweight multi-agent system for prioritizing investment research.
Its job is not to make autonomous buy or sell decisions. Its job is to help a researcher decide:

1. which tickers are worth reviewing first today
2. why they matter now
3. what evidence supports or challenges that view
4. what is still missing before deeper analysis

The system is designed around short feedback loops. A good output is not just plausible. It must be:

- evidence-grounded
- easy to review
- easy to revisit later
- useful for deciding where to spend research time

---

## Product Goal

The primary goal is to turn a noisy stream of market news into a ranked watchlist for daily research triage.

For each ticker in a watchlist, the system should produce a triage card with:

- `priority`: `high`, `medium`, or `low`
- `why_now`: why this ticker deserves attention now
- `key_evidence`: the most relevant supporting evidence
- `counter_evidence`: the main conflicting or cautionary evidence
- `missing_questions`: what still needs verification
- `next_action`: whether this ticker is worth a deeper 10-minute review

The final output of one run is a ranked list of the most important tickers to review first.

---

## Design Principles

1. Keep each agent narrowly scoped to one cognitive task.
2. Make intermediate outputs inspectable and storable.
3. Prefer ticker-specific evidence over generic market noise.
4. Separate evidence collection, signal structuring, prioritization, and critique.
5. Build for fast feedback: same-day review and short-horizon follow-up.
6. Treat the ledger and evaluation set as first-class assets.

---

## Who This Is For

This system is for a human researcher working through a fixed watchlist under time pressure.

It is useful when the user asks:

- What should I look at first today?
- Which names have real ticker-specific developments?
- Which names only look interesting because of broad macro noise?
- Which names have enough evidence for a deeper pass?

This system is not trying to replace full investment judgment, valuation work, or portfolio construction.

---

## Agent Overview

The environment contains five core agents:

1. Retrieval Agent
2. Event Structuring Agent
3. Triage Agent
4. Reviewer Agent
5. Evaluation Agent

---

## 1. Retrieval Agent

### Purpose
Find the most relevant recent evidence for each ticker in the watchlist.

### Responsibility
The Retrieval Agent builds ticker-focused queries, searches the document store, and returns candidate evidence.

### Input
```json
{
  "ticker": "NVDA",
  "window": "last 3 days"
}
```

### Output
```json
{
  "ticker": "NVDA",
  "retrieved_docs": [
    {
      "article_id": 101,
      "title": "Nvidia supplier commentary signals continued AI demand",
      "url": "https://example.com/article",
      "summary": "Demand commentary remains strong.",
      "content": "..."
    }
  ]
}
```

### Boundary
This agent does not interpret the documents or assign a ticker priority.

---

## 2. Event Structuring Agent

### Purpose
Convert retrieved evidence into normalized, investment-relevant signals.

### Responsibility
The Event Structuring Agent reads retrieved documents and extracts structured events.

### Output Schema
```json
{
  "article_id": 101,
  "event_type": "Company | Macro | Sector | Market",
  "direction": "Positive | Negative | Neutral",
  "importance": "High | Medium | Low",
  "time_horizon": "Short-term | Long-term | Both",
  "affected_asset": "NVDA | General Market",
  "reasoning": "Supplier commentary suggests continued demand strength.",
  "evidence_excerpt": "Management said AI-related orders remain strong into next quarter."
}
```

### Boundary
This agent does not rank the ticker and does not decide whether it is worth reviewing.

---

## 3. Triage Agent

### Purpose
Turn structured signals into a research-priority recommendation for one ticker.

### Responsibility
The Triage Agent aggregates retrieved evidence and structured events into a single triage card.

### Input
```json
{
  "ticker": "NVDA",
  "retrieved_docs": [],
  "structured_events": []
}
```

### Output Schema
```json
{
  "ticker": "NVDA",
  "priority": "High",
  "confidence": "Medium",
  "why_now": "Recent company-specific evidence suggests continued AI demand strength.",
  "key_evidence": [
    "Supplier commentary pointed to sustained AI server demand.",
    "Recent reporting highlighted strong demand visibility into next quarter."
  ],
  "counter_evidence": [
    "Valuation sensitivity remains a risk if broader market sentiment weakens."
  ],
  "missing_questions": [
    "Is demand strength broad-based or concentrated in a few customers?"
  ],
  "next_action": "Worth a deeper review of demand durability and margin implications."
}
```

### Boundary
This agent does not perform full valuation, full portfolio construction, or final buy/sell execution logic.

---

## 4. Reviewer Agent

### Purpose
Challenge the triage card before it reaches the user.

### Responsibility
The Reviewer Agent checks whether the triage output is evidence-grounded, specific, and actionable.

### Review Questions
The Reviewer Agent should challenge:

1. Is the evidence too generic?
2. Is the output relying on broad macro noise instead of ticker-specific signals?
3. Does the reasoning jump beyond the evidence?
4. Is key counter-evidence missing?
5. Is the next action concrete enough to be useful?

### Output
```json
{
  "evidence_too_generic": false,
  "missing_target_specific_signal": false,
  "reasoning_jump": false,
  "next_action_too_vague": false,
  "summary": "The triage case is mostly well supported, but valuation sensitivity should remain explicit.",
  "should_flag_human_review": false
}
```

### Boundary
This agent does not replace the Triage Agent. It critiques and adjusts confidence, but it does not own the primary triage decision.

---

## 5. Evaluation Agent

### Purpose
Turn daily usage and human review into a feedback loop.

### Responsibility
The Evaluation Agent analyzes stored triage runs and human feedback to identify failure patterns and measure usefulness.

### Core Metrics

- `precision@3`: how many top-3 names were actually worth reviewing
- `overlap@3`: overlap between system top-3 and human top-3
- `miss_rate`: important names the system failed to rank highly
- `specificity_rate`: share of top names supported by ticker-specific evidence
- `reasoning_pass_rate`: share of top names without clear reasoning jumps
- `follow_through_rate`: share of top names that still looked worth tracking after short follow-up

### Boundary
This agent does not generate the watchlist itself. It exists to improve later runs.

---

## End-to-End Flow

```text
Watchlist
  -> Retrieval Agent
  -> Event Structuring Agent
  -> Triage Agent
  -> Reviewer Agent
  -> Ranked Triage Output
  -> Human Review
  -> Evaluation Agent
  -> Prompt / retrieval / workflow improvements
```

---

## Ledger and Feedback Loop

Every triage run should be persisted.

For each ticker, the system should store:

- run date
- retrieved evidence
- reranked evidence
- structured events
- final triage card
- reviewer findings
- model and prompt versions

Human review should then add:

- whether the ticker was actually worth reviewing
- whether the evidence was ticker-specific
- whether the reasoning was sound
- whether an important ticker was missed
- whether follow-up evidence over the next 1-3 days supported the priority

This creates the core feedback loop:

```text
triage
  -> human review
  -> short-term follow-up
  -> failure analysis
  -> system update
```

This is the project's main notion of "closed loop." The loop is not "did the stock go up?" The loop is "did the system help the researcher focus attention on the right names, for the right reasons, and can we improve it when it fails?"

---

## Minimal Success Criteria

The first useful version of this project should be able to:

1. read a watchlist of 10-20 tickers
2. retrieve recent evidence for each ticker
3. produce a triage card for each ticker
4. rank the watchlist by research priority
5. store the output in a ledger
6. accept human review feedback
7. report simple usefulness metrics over multiple runs

---

## What This Project Is Not

This project is not:

- a fully autonomous trading system
- a claim of consistent alpha generation
- a substitute for valuation work, management assessment, or portfolio sizing
- a generic chatbot for financial Q&A

It is a research workflow system built to prioritize attention, preserve evidence, and learn from fast feedback.
