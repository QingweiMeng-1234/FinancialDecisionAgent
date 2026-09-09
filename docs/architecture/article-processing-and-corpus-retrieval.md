# Article processing and corpus retrieval

## Ownership

News Ingestion owns derived-stage completion through `article_processing.process_article`.
Normal ingestion, summary backfill, theme acquisition, and content backfill use this module.
The caller supplies summarization and vector adapters; the module owns state changes and
the content version against which a result may be published.

- Unchanged content does not imply completed processing. Failed or pending stages resume.
- A completed summary is reused when only the index needs repair; no LLM client is created
  for an index-only repair.
- A new summary invalidates index readiness until its vector metadata is rebuilt.
- Index readiness requires a matching content hash. Legacy `ready` rows without that hash
  are rebuilt when processed.
- Summary and index completion/failure updates use the expected content version. A task
  working on an older body cannot mark the newer body's stages ready or failed.
- Rejected, missing, and hash-mismatched canonical content cannot enter derived processing.
- Stage errors remain separate: an index failure does not turn a successful summary into
  a summary failure. Ingestion retains partial results; backfill callers keep their existing
  error/reporting conventions.

Evidence Retrieval constructs production search stores through
`corpus_retrieval.create_corpus_vector_store(db_path=..., ...)`. Query, recommendation,
watchlist and theme CLIs, MCP, refresh-then-query, and retrieval evaluation share this path.
Direct `ChromaVectorStore` construction remains available for indexing, migration, and
isolated adapter tests. Library callers injecting their own stores must supply an equally
qualified corpus adapter when serving canonical evidence.

For each search, SQLite supplies eligible article IDs and content hashes after checking
current processing state and canonical files. Chroma intersects these IDs with any caller
filter and rejects chunks whose hashes differ from the eligible version. Knowledge of how
to connect this validation belongs to the shared constructor, not individual entry points.
Each eligibility read creates and closes its SQLite connection in the searching thread;
watchlist workers never borrow the orchestrator's connection.

## Existing data

Existing rows whose index status says `ready` but whose indexed content hash is missing
will now be excluded from production retrieval. A normal refresh or
`python summarize_articles.py --db-path ... --persist-dir ...` can rebuild their derived
stages. The summary command also repairs missing indexes for already summarized articles.
It does not refetch invalid bodies; those require ingestion/content backfill first.
No live corpus migration or bulk reindex was performed as part of this change.

## Verification

Expected failing tests were observed before implementation for:

- identical refresh after summary failure, index failure, or an omitted index;
- missing version hashes in summary, theme, and content backfill outputs;
- resuming backfill work without repeating a successful summary or needing an LLM key;
- old processing results arriving after a content update;
- rejected/missing/tampered content reaching processing;
- rebuilding a summary without immediately rebuilding its index metadata;
- invalid corpus entries escaping MCP and other workflow entry points;
- stale vector content under an otherwise eligible article ID.

Focused tests then passed, followed by regression over the tracked core test suite and
`tests/test_corpus_retrieval.py`. Tests use temporary SQLite/Chroma stores, deterministic
embeddings, and substituted external model/search adapters. Additional verification covers
bounded threaded retrieval and intersection with caller article filters.

Final local verification on 2026-09-09: **299 passed, 3 skipped**. The three skips are
pre-existing Event collector tests marked temporarily disabled. Python syntax parsing
passed for all 27 changed Python files, and `git diff --check` passed. Untracked user
evaluation work was preserved and excluded from this core regression run.

These checks do not establish production data quality, external-provider behavior, or a
latency SLA. Eligibility is checked at search time; it is not a transaction spanning
SQLite, filesystem, Chroma, and the final answer. No claim of distributed atomicity is made.
This change does not implement KB v2 release pinning or watchlist run deadlines.
