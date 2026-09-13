# Retrieval golden data sync (2026-09-13)

This data snapshot supports `annotations/retrieval_eval_master.yaml` (36 articles).
Run commands from the repository root. Annotation paths and available legacy DB
article paths use repository-relative paths. Article 225 labels QQQ as indirect.

| Asset | Records | Golden coverage |
| --- | ---: | ---: |
| `news_articles.db` | 482 articles | 36/36 |
| `chroma_data/`, collection `news_articles` | 2817 chunks | 36/36 |
| `data/rag_corpus_v2_20260815/news_articles.db` | 1353 articles | 36/36 |
| `data/rag_index_v2_20260815/` | 6166 chunks across two collections | 34/36 across collections |

The legacy index was rebuilt from all 2817 persisted chunk documents and metadata
using locally cached `all-MiniLM-L6-v2` embeddings and cosine distance because its
HNSW files were missing. Counts and an actual vector query were checked.

The versioned index is preserved as-is: article 177 is pending and article 228 is
quarantined (`source_ready_ready_hash_mismatch`). Neither is made eligible by this
sync. Use the legacy database/index pair for the 36-article retrieval fixture;
the Theme-Research CLI defaults to the versioned corpus/index instead.

This commit ships local data and index files, not just source text. SQLite
integrity checks and golden-ID coverage checks passed. Both versioned collections
were tested with vector queries on a copy. Full retrieval quality evaluation was
not rerun. Vector chunk counts are not article counts.
