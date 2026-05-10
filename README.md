# Financial Agent

Minimal financial-news RAG pipeline with:

- news collection
- SQLite storage
- article summarization
- Chroma vector indexing
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

Optional model overrides:

```powershell
$env:OPENAI_ANSWER_MODEL="gpt-5.4"
$env:OPENAI_RERANK_MODEL="gpt-5.4-mini"
```

If you do not set the per-agent model vars, the defaults are:

- answering: `gpt-5.4`
- reranking: `gpt-5.4-mini`

## End-To-End Run

This is the main E2E command. It will:

1. collect news
2. store articles in SQLite
3. summarize the articles
4. index them in Chroma
5. retrieve candidates for your question
6. rerank the candidates
7. generate a grounded answer

Run:

```powershell
python main.py --question "What are the biggest themes in the latest news?" --debug-rerank
```

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

## Summarization Backfill

If articles already exist in SQLite and you want to fill in missing summaries:

```powershell
python summarize_articles.py --db-path news_articles.db --persist-dir .\chroma_data
```

Useful variants:

```powershell
python summarize_articles.py --limit 10
python summarize_articles.py --source news
python summarize_articles.py --force
```

## Event Structuring

To derive normalized market events from stored articles:

```powershell
python structure_events.py --db-path news_articles.db
```

## Notes

- `OPENAI_API_KEY` is required for summarization, reranking, and answer generation.
- `NEWSAPI_API_KEY` is required for live news collection through `NewsCollector`.
- `main.py` currently includes `ManualCollector`, so the run may prompt for manual input depending on your session flow.

## TODO

### RAG

- Add chunking to the retrieval pipeline.
- Add evaluation methods for retrieval, reranking, and grounded answer quality.

### Decision

- Add an agent-driven decision flow that uses an agent plus a Buffett-style skill to make recommendations for user-specified stocks.

### Storage

- Remove stale news from storage and keep only articles from the last 3 months.
