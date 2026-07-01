#!/usr/bin/env python3
"""Backward-compatible wrapper for the news ingestion CLI."""

from ingest_news import main


if __name__ == "__main__":
    raise SystemExit(main())
