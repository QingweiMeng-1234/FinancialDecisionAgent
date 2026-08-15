#!/usr/bin/env python3
"""Compatibility wrapper for the package-native theme research CLI."""

import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
load_dotenv()

from event_collector.cli.theme_research import main, parse_args, parse_ticker_hints, render_theme_research_summary


if __name__ == "__main__":
    raise SystemExit(main())
