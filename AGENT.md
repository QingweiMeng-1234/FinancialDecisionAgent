# AGENT.md

## Project: Financial Multi-Agent Reasoning Environment

This project is a lightweight multi-agent system for reasoning about market events and producing an explainable investment decision. The goal is not to predict prices directly, but to convert messy market information into structured signals, aggregate those signals, and make a decision using a consistent investment framework.

## Design Principles

1. Keep each agent narrowly scoped.
2. Make every intermediate output inspectable.
3. Prefer structured reasoning over vague market commentary.
4. Separate short-term market noise from long-term business fundamentals.
5. Keep the system simple enough to demo, but modular enough to extend.

---

## Agent Overview

The environment contains four core agents:

1. Event Collector Agent
2. Event Structuring Agent
3. Aggregation Agent
4. Decision Agent

Optional future extension:

5. Risk Agent

---

## 1. Event Collector Agent

### Purpose
Collect the raw market events that the system will reason about.

### Responsibility
The Event Collector Agent gathers relevant daily market information from manual input, news snippets, APIs, or user-provided descriptions.

### Input
```json
{
  "source": "manual | news | api",
  "raw_text": "Fed signals higher rates for longer after inflation data comes in above expectations."
}
```

### Output
```json
{
  "events": [
    {
      "id": "event_001",
      "raw_text": "Fed signals higher rates for longer after inflation data comes in above expectations."
    }
  ]
}
```

### Boundary
This agent does not interpret whether the event is good or bad. It only collects and normalizes raw event input.

---

## 2. Event Structuring Agent

### Purpose
Convert unstructured market information into standardized decision signals.

### Responsibility
The Event Structuring Agent reads each raw event and extracts a structured representation.

### Input
```json
{
  "id": "event_001",
  "raw_text": "Fed signals higher rates for longer after inflation data comes in above expectations."
}
```

### Output Schema
```json
{
  "event_id": "event_001",
  "event_type": "Macro | Company | Sector | Market",
  "direction": "Positive | Negative | Neutral",
  "importance": "High | Medium | Low",
  "time_horizon": "Short-term | Long-term | Both",
  "affected_asset": "AAPL | MSFT | SPY | General Market",
  "reasoning": "Higher expected rates may pressure equity valuations, especially growth stocks."
}
```

### Example
```json
{
  "event_id": "event_001",
  "event_type": "Macro",
  "direction": "Negative",
  "importance": "High",
  "time_horizon": "Short-term",
  "affected_asset": "General Market",
  "reasoning": "Higher interest rates increase discount rates and can reduce equity valuations."
}
```

### Boundary
This agent does not make a final investment decision. It only classifies and explains individual events.

---

## 3. Aggregation Agent

### Purpose
Combine multiple structured events into a system-level market view.

### Responsibility
The Aggregation Agent weighs signals, identifies conflicts, and determines the dominant driver.

### Scoring Rule
Basic scoring:

```text
Positive = +1
Neutral  =  0
Negative = -1
```

Importance multiplier:

```text
High   = 3
Medium = 2
Low    = 1
```

Final event score:

```text
event_score = direction_score * importance_multiplier
```

### Input
```json
{
  "structured_events": [
    {
      "event_type": "Macro",
      "direction": "Negative",
      "importance": "High",
      "time_horizon": "Short-term"
    },
    {
      "event_type": "Company",
      "direction": "Positive",
      "importance": "High",
      "time_horizon": "Long-term"
    }
  ]
}
```

### Output
```json
{
  "macro_score": -3,
  "company_score": 3,
  "sector_score": 0,
  "market_score": 0,
  "net_score": 0,
  "dominant_driver": "Mixed: macro pressure offsets company strength",
  "conflicts": [
    "Short-term macro headwind conflicts with long-term company fundamentals."
  ],
  "summary": "The overall signal is mixed. Macro conditions are negative, but company-specific fundamentals are positive."
}
```

### Boundary
This agent does not say BUY, HOLD, or SELL. It prepares the reasoning context for the Decision Agent.

---

## 4. Decision Agent — Buffett Lens

### Purpose
Make the final investment judgment using a consistent long-term investment framework.

### Responsibility
The Decision Agent interprets the aggregated signal through a Buffett-style lens: business quality, long-term fundamentals, margin of safety, and market overreaction.

### Decision Rules
The Decision Agent should:

1. Separate temporary market pressure from durable business impairment.
2. Favor strong long-term fundamentals over short-term noise.
3. Avoid buying when the signal is positive but uncertainty is too high.
4. Explain confidence rather than pretending certainty.

### Input
```json
{
  "macro_score": -3,
  "company_score": 3,
  "net_score": 0,
  "dominant_driver": "Mixed: macro pressure offsets company strength",
  "conflicts": [
    "Short-term macro headwind conflicts with long-term company fundamentals."
  ]
}
```

### Output Schema
```json
{
  "decision": "BUY | HOLD | SELL",
  "confidence": 0.7,
  "time_horizon": "Short-term | Long-term",
  "reasoning": "Short-term macro pressure is negative, but long-term company fundamentals remain strong.",
  "key_risk": "If rates remain elevated longer than expected, valuation pressure may persist."
}
```

### Example Output
```json
{
  "decision": "HOLD",
  "confidence": 0.65,
  "time_horizon": "Long-term",
  "reasoning": "The signal is mixed: macro conditions are unfavorable, but company-specific fundamentals remain strong. A Buffett-style framework would avoid overreacting to short-term macro noise.",
  "key_risk": "Persistent high interest rates could continue to pressure valuation multiples."
}
```

---

## Optional Future Agent: Risk Agent

### Purpose
Adjust confidence and flag downside risks before the final decision is presented.

### Responsibility
The Risk Agent reviews the Aggregation Agent and Decision Agent outputs to identify concentration risk, uncertainty, downside scenarios, and missing information.

### Output
```json
{
  "risk_level": "Low | Medium | High",
  "confidence_adjustment": -0.1,
  "risk_notes": [
    "The decision depends heavily on one company-specific positive event.",
    "Macro conditions remain uncertain."
  ]
}
```

This agent is not required for the first implementation. It is a natural extension if the system needs stronger risk control.

---

## End-to-End Flow

```text
Raw Market Event
      ↓
Event Collector Agent
      ↓
Event Structuring Agent
      ↓
Aggregation Agent
      ↓
Decision Agent
      ↓
Final Investment Decision
```

---

## Minimal Demo Flow

### Input
```text
Apple reports stronger-than-expected earnings, but the Fed signals that interest rates may stay high for longer.
```

### Event Structuring Output
```json
[
  {
    "event_type": "Company",
    "direction": "Positive",
    "importance": "High",
    "time_horizon": "Long-term",
    "reasoning": "Stronger earnings suggest durable business performance."
  },
  {
    "event_type": "Macro",
    "direction": "Negative",
    "importance": "High",
    "time_horizon": "Short-term",
    "reasoning": "Higher rates can pressure equity valuations."
  }
]
```

### Aggregation Output
```json
{
  "macro_score": -3,
  "company_score": 3,
  "net_score": 0,
  "dominant_driver": "Mixed signal",
  "conflicts": [
    "Company fundamentals are positive, but macro conditions are negative."
  ]
}
```

### Final Decision Output
```json
{
  "decision": "HOLD",
  "confidence": 0.65,
  "reasoning": "The company-specific signal is strong, but macro uncertainty offsets the near-term upside. A long-term investor would monitor valuation rather than overreacting.",
  "key_risk": "If high interest rates persist, valuation multiples may compress."
}
```

---
