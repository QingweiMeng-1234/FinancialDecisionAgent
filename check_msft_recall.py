#!/usr/bin/env python3
"""Quick SQLite-based recall sanity check for MSFT identity coverage."""

from __future__ import annotations

import argparse
import sqlite3
from typing import Iterable


DEFAULT_QUERIES = ["msft", "microsoft", "azure", "satya nadella"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a quick lexical recall check against the SQLite article store."
    )
    parser.add_argument(
        "--db-path",
        default="news_articles.db",
        help="Path to the SQLite database file.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="How many article rows to print for each detailed section.",
    )
    parser.add_argument(
        "--queries",
        nargs="+",
        default=DEFAULT_QUERIES,
        help="Query terms to compare for hit counts.",
    )
    return parser.parse_args()


def build_article_text_expression() -> str:
    return (
        "lower("
        "coalesce(title, '') || ' ' || "
        "coalesce(description, '') || ' ' || "
        "coalesce(content, '') || ' ' || "
        "coalesce(summary, '')"
        ")"
    )


def print_tables_and_columns(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    print("Tables:")
    for row in cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        print(f"- {row[0]}")

    print("\nArticles columns:")
    for row in cur.execute("PRAGMA table_info(articles)"):
        print(row)


def print_recent_articles(conn: sqlite3.Connection, *, limit: int) -> None:
    cur = conn.cursor()
    print(f"\nRecent article samples (limit={limit}):")
    for row in cur.execute(
        """
        SELECT id, published_at, title, url
        FROM articles
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    ):
        print(row)


def print_query_hit_counts(conn: sqlite3.Connection, queries: Iterable[str]) -> None:
    cur = conn.cursor()
    article_text = build_article_text_expression()
    sql = f"""
    SELECT COUNT(*)
    FROM articles
    WHERE {article_text} LIKE ?
    """

    print("\nQuery hit counts:")
    for query in queries:
        count = cur.execute(sql, (f"%{query.lower()}%",)).fetchone()[0]
        print(f"- {query}: {count}")


def print_matching_articles(conn: sqlite3.Connection, *, query: str, limit: int) -> None:
    cur = conn.cursor()
    article_text = build_article_text_expression()
    sql = f"""
    SELECT id, published_at, title, url
    FROM articles
    WHERE {article_text} LIKE ?
    ORDER BY published_at DESC
    LIMIT ?
    """

    print(f"\nArticles matching '{query}' (limit={limit}):")
    for row in cur.execute(sql, (f"%{query.lower()}%", limit)):
        print(row)


def print_gap_articles(conn: sqlite3.Connection, *, include_term: str, exclude_term: str, limit: int) -> None:
    cur = conn.cursor()
    article_text = build_article_text_expression()
    sql = f"""
    SELECT id, published_at, title, url
    FROM articles
    WHERE {article_text} LIKE ?
      AND {article_text} NOT LIKE ?
    ORDER BY published_at DESC
    LIMIT ?
    """

    print(f"\nArticles matching '{include_term}' but not '{exclude_term}' (limit={limit}):")
    for row in cur.execute(sql, (f"%{include_term.lower()}%", f"%{exclude_term.lower()}%", limit)):
        print(row)


def main() -> int:
    args = parse_args()
    conn = sqlite3.connect(args.db_path)
    try:
        print_tables_and_columns(conn)
        print_recent_articles(conn, limit=min(args.limit, 10))
        print_query_hit_counts(conn, args.queries)
        print_matching_articles(conn, query="microsoft", limit=args.limit)
        print_gap_articles(conn, include_term="microsoft", exclude_term="msft", limit=args.limit)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
