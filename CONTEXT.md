# Context

## Product

Financial Agent is a news-grounded research workflow for three adjacent jobs:

- refresh and index recent market news
- answer grounded research questions from retrieved evidence
- rank a watchlist so a human researcher knows which tickers deserve attention first

## Core Modules

### News Ingestion

The News Ingestion module collects raw market inputs, materializes canonical article records, fetches article content, summarizes it, and indexes it for retrieval.

Primary seams:

- `event_collection.py`: raw Event collection and article materialization
- `news_ingestion.py`: collect-and-ingest workflow
- `news_pipeline.py`: refresh workflow plus optional immediate question-answering

### Evidence Retrieval

The Evidence Retrieval module turns a query or ticker into a `RetrievalEvidenceBundle`.

Its interface should hide:

- retrieval intent normalization
- company-aware query expansion
- vector search result cleanup
- rerank candidate shaping
- reranker execution
- evidence shaping for downstream modules

Primary seams:

- `retrieval_orchestration.py`: bundle construction and scoring rules
- `retrieval_execution.py`: bounded retrieval execution for one or many work items

### Grounded Answering

The Grounded Answering module consumes retrieved evidence and produces a cited answer without using outside knowledge.

Primary seam:

- `rag_answering.py`

### Recommendation

The Recommendation module turns retrieved evidence plus structured events into a Buffett-lens recommendation for one target.

Primary seam:

- `recommendation.py`

### Watchlist Research

The Watchlist Research module ranks a list of tickers by research priority. It is not a trading module.

It combines:

- Evidence Retrieval
- Event Structuring
- Triage
- Review
- feedback persistence

Primary seams:

- `watchlist_research.py`: batched retrieval and orchestration
- `watchlist_triage.py`: triage domain types, rendering, and compatibility seam

### MCP Surface

The MCP Surface exposes the main refresh, retrieval, recommendation, and watchlist workflows to external tools.

Primary seam:

- `financial_agent_mcp.py`

## Repo Seams

### Package Code

Application and domain code lives under `src/event_collector/`.

### CLI Entry Points

Package-native CLI implementations live under `src/event_collector/cli/`.
Root-level `*.py` files are compatibility wrappers and should stay thin.

### Runtime Data

Generated reports belong under `reports/`.
Working data, indexes, and databases should converge under data-oriented directories rather than growing new root-level artifacts.

### Experiments

Exploratory or adjacent projects such as `rag-from-zero/` and `QuantGPT/` are separate modules at the repo seam, not part of the core Financial Agent package.
