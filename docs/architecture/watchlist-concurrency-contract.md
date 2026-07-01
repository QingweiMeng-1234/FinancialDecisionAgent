# Watchlist Concurrency Contract

## Why This Exists

The watchlist workflow now relies on bounded concurrency to avoid OpenClaw timeouts, but that concurrency is only acceptable when resource ownership and failure handling stay explicit. This note defines the contract the implementation must follow.

## Concurrency Risks

- `vector store` objects are not assumed thread-safe by default.
- `SQLite` persistence can lock or corrupt behavior if writes are scattered across uncontrolled worker threads.
- LLM-backed stages can amplify rate limits, retries, and timeout storms if fan-out is unbounded.
- concurrent completion order must not leak into final ticker ordering or silently drop work.

## Resource Ownership Rules

- `watchlist research` is the single orchestrator for a run.
- `retrieval execution` owns retrieval worker scheduling and per-work-item failure normalization.
- `SQLiteNewsStore` persistence stays on orchestrator-controlled boundaries.
- watchlist structuring is a shared stage, not a per-ticker side effect hidden inside later LLM stages.

## Safe Sharing Rules

- concurrent retrieval must not assume a shared vector-store instance is safe unless the caller explicitly opts into a proven `shared_read` model.
- the default safe model for real watchlist MCP runs is per-worker isolated vector-store instances supplied by a factory.
- provider-factory initialization may be serialized inside the execution engine when the underlying runtime is not safe to construct concurrently.
- bounded concurrency is required for retrieval, triage, and review. Unbounded fan-out is not allowed.
- shared article structuring results may be reused across tickers inside one run, but each unique article is structured at most once per run.

## Failure and Timeout Policy

- one slow or failed ticker must not abort the entire watchlist run by default.
- retrieval failures become structured per-ticker failures plus fallback records in the final result.
- downstream stage failures must degrade to human-review-safe fallback output rather than whole-run timeout.
- OpenClaw no-timeout is the primary acceptance goal for normal watchlist usage.

## Deterministic Merge Requirements

- all input tickers must appear in the final result object.
- final result ordering must remain deterministic regardless of worker completion order.
- partial failures must be visible in structured output; no ticker may be silently skipped.
