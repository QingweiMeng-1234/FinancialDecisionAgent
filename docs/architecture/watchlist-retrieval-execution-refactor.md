# Watchlist Retrieval Execution Refactor

## What Changed

- `watchlist research` now owns watchlist batching, merge policy, and partial retrieval failure handling.
- `retrieval execution` is a separate seam for bounded fan-out `retrieve + rerank` work.
- `financial_agent_mcp.py` now delegates watchlist orchestration instead of owning watchlist batching logic.
- `watchlist research` now runs as an explicit staged workflow:
  1. full-ticker retrieval
  2. shared article structuring
  3. bounded-concurrency triage
  4. bounded-concurrency review
  5. final merge and ranking
- shared article structuring is now a first-class run stage rather than a ticker-loop side effect.

## Why V1 Starts Here

`watchlist research` and `recommendation` both depended directly on `retrieve_evidence_bundle(...)`, so they were the best first callers to move behind a shared retrieval execution seam. This keeps the concurrency policy and failure normalization in one place without forcing a broad transport or streaming redesign.

## Why `query_news_research` Waits

`query_news_research` still stays on its existing path in v1. Its caller contract is stable today, and we do not need to force it through multi-item fan-out to unlock the watchlist performance work. The next migration should happen only if these seams remain stable:

- bounded worker-pool retrieval execution remains synchronous to callers
- per-work-item failure normalization stays structured and deterministic
- recommendation and watchlist continue to use the same `retrieve + rerank` contract

## Stable Seams to Preserve

- `watchlist research` returns one complete result object and owns internal batching
- `retrieval execution` owns ticker-level fan-out, bounded concurrency, and error normalization
- MCP remains an adapter layer for parameter mapping, response mapping, and transport configuration

## V2 Completion Notes

- all tickers still pass through the workflow even when execution is staged internally
- OpenClaw no-timeout is the primary acceptance target; raw benchmark improvement is now secondary
- real MCP watchlist runs should prefer a vector-store factory rather than a shared vector-store instance so the retrieval execution seam can use isolated worker resources
- `query_news_research` still stays on its existing path; this refactor intentionally deepens watchlist research and recommendation first
