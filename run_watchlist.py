#!/usr/bin/env python3
"""Compatibility wrapper for the package-native watchlist CLI."""

import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
load_dotenv()

from event_collector.cli.run_watchlist import main, parse_args, parse_requested_tickers


if __name__ == "__main__":
    raise SystemExit(main())
