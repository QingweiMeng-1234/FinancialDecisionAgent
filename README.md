# Financial Agent

Minimal financial-news RAG pipeline with:

- news collection
- package-native CLI entrypoints
- SQLite reference / identity storage
- filesystem-backed exact article content storage
- article summarization
- Chroma chunk-level vector indexing
- retrieval + reranking
- grounded answer generation

## Setup

Create and activate a virtual environment, then install dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Set the required environment variables:

```powershell
$env:OPENAI_API_KEY="your-openai-key"
$env:NEWSAPI_API_KEY="your-newsapi-key"
```

Project context and module seams now live in [CONTEXT.md](./CONTEXT.md).

## Architecture and Contracts

- [RAG Ingestion and Generation Serving PRD](./docs/product/rag-ingestion-serving-prd.md)
- [RAG Ingestion and Generation Serving System Design](./docs/architecture/rag-ingestion-serving-system-design.md)
- [RAG Ingestion and Serving API and Data Contract](./docs/api/rag-ingestion-serving-api.md)
- [RAG v1.0 freeze manifest](./docs/product/rag-ingestion-serving-freeze-manifest-v1.0.json)
- [RAG Corpus Migration Backup and Rollback Runbook](./docs/operations/rag-corpus-migration-runbook.md)
- [RAG reliability remediation history and acceptance criteria](./docs/architecture/rag-ingestion-reliability-remediation-plan.md)

Optional model overrides:

```powershell
$env:OPENAI_ANSWER_MODEL="deepseek-v4-pro"
$env:OPENAI_RERANK_MODEL="deepseek-v4-flash"
```

If you do not set the per-agent model vars, the defaults are:

- answering: `deepseek-v4-pro`
- reranking: `deepseek-v4-flash`

## End-To-End Run

This is the main E2E command. It will:

1. collect news
2. store articles in SQLite
3. fetch and store exact article content on disk
4. summarize the articles
5. index fixed-size content chunks in Chroma
6. retrieve candidates for your question
7. rerank the candidates
8. generate a grounded answer

Run:

```powershell
python main.py --question "What are the biggest themes in the latest news?" --debug-rerank
```

Implementation note:

- root-level `*.py` files are compatibility wrappers
- package-native CLI implementations live under `src/event_collector/cli/`
- the collect-and-ingest workflow lives in `src/event_collector/news_ingestion.py`
- retrieval fan-out and failure handling live in `src/event_collector/retrieval_execution.py`

You should see:

- ingestion stats
- total stored article count
- the final grounded answer
- supporting and counter points when present
- source snippets
- rerank debug output if `--debug-rerank` is enabled

## Query Existing Data

If articles are already indexed, you can query them without re-ingesting:

```powershell
python query_news.py --question "What changed in the latest news?" --debug-rerank
```

## Stock Recommendation Reports

To generate a news-grounded Buffett-style recommendation for one target and save a Markdown report in the repo:

```powershell
python recommend_stock.py --target "MSFT" --debug-rerank --debug-aggregation
```

By default, reports are written to `reports/recommendations/` with names like:

```text
2026-05-10_203015_MSFT.md
```

## Canonical Summary and Content Repair

If canonical articles already exist and you want to fill in missing summaries:

```powershell
python summarize_articles.py --db-path .\data\rag_corpus_v2_20260815\news_articles.db
```

Useful variants:

```powershell
python summarize_articles.py --limit 10
python summarize_articles.py --source news
python summarize_articles.py --force
```

To repair missing publisher content, use the canonical-only content backfill:

```powershell
python backfill_article_content.py --db-path .\data\rag_corpus_v2_20260815\news_articles.db
```

These repair commands never open or mutate a serving Chroma collection. After
canonical rows change, run the verified successor-generation refresh before
expecting the changes to appear in RAG queries. The refresh builds a new
immutable generation, verifies it, and activates it with the control pointer;
do not update `chroma_data/news_articles` in place.

## Event Structuring

To derive normalized market events from stored articles:

```powershell
python structure_events.py --db-path news_articles.db
```

## Notes

- `OPENAI_API_KEY` is required for summarization, reranking, and answer generation.
- `NEWSAPI_API_KEY` is required for live news collection through `NewsCollector`.
- `main.py` currently includes `ManualCollector`, so the run may prompt for manual input depending on your session flow.
- SQLite stores article identity/reference data such as URLs, content paths, hashes, summaries, and processing status.
- Exact article content is stored on disk at `data/articles/{article_id}.txt`.
- Chroma stores chunk-level vectors with record ids shaped like `{article_id}:{chunk_index}`.
- Current chunking is fixed-size with overlap. Semantic chunking is still a TODO.

## TODO

### RAG

- Upgrade fixed-size chunking to semantic chunking.
- Add evaluation methods for retrieval, reranking, and grounded answer quality.

### Decision

- Add an agent-driven decision flow that uses an agent plus a Buffett-style skill to make recommendations for user-specified stocks. (Done)

### Storage

- Remove stale news from storage and keep only articles from the last 3 months.
- Dedupe events that are downloaded NewsAPI
