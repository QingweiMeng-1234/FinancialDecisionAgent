#!/usr/bin/env python3
"""Render the LangChain-backed document pipeline workflow in the CLI."""

from __future__ import annotations

import argparse

from langchain_core.runnables import RunnableLambda


def build_document_pipeline_workflow():
    return (
        RunnableLambda(lambda x: x, name="NewsArticle")
        | RunnableLambda(lambda x: x, name="LangChain Document")
        | RunnableLambda(lambda x: x, name="TextSplitter")
        | RunnableLambda(lambda x: x, name="Chunk Documents")
        | RunnableLambda(lambda x: x, name="Embeddings")
        | RunnableLambda(lambda x: x, name="Chroma")
    )


def parse_args():
    parser = argparse.ArgumentParser(description="Visualize the LangChain document pipeline workflow.")
    parser.add_argument(
        "--format",
        choices=("ascii", "mermaid", "both"),
        default="both",
        help="Which graph format to print",
    )
    parser.add_argument(
        "--show-metadata",
        action="store_true",
        help="Also print a CLI-friendly metadata flow view",
    )
    return parser.parse_args()


def build_metadata_view() -> str:
    return """=== Metadata Flow View ===
[NewsArticle]
  content
  title / url / source / published_at / summary / content_sha256
        |
        v
[LangChain Document]
  page_content = normalized article content
  metadata = {
    article_id,
    title,
    url,
    original_url,
    canonical_url,
    source,
    published_at,
    summary,
    content_sha256
  }
        |
        v
[TextSplitter]
  strategy = RecursiveCharacterTextSplitter
  params   = chunk_size + chunk_overlap
        |
        v
[Chunk Documents]
  page_content = one chunk
  metadata = inherited article metadata + chunk_index
        |
        v
[Vector Store]
  responsibilities:
  - embed chunk texts
  - upsert ids/documents/metadatas into Chroma
  - search and aggregate chunk hits back to article level
"""


def main() -> int:
    args = parse_args()
    graph = build_document_pipeline_workflow().get_graph()

    if args.format in {"ascii", "both"}:
        print("=== ASCII Workflow ===")
        print(graph.draw_ascii())

    if args.format in {"mermaid", "both"}:
        if args.format == "both":
            print()
        print("=== Mermaid Workflow ===")
        print(graph.draw_mermaid())

    if args.show_metadata:
        print()
        print(build_metadata_view())

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
