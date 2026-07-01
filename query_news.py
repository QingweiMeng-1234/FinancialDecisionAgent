#!/usr/bin/env python3
"""Compatibility wrapper for the package-native query CLI."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from event_collector.cli.query_news import main, parse_args, render_rag_answer, run_question


if __name__ == "__main__":
    raise SystemExit(main())
