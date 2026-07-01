#!/usr/bin/env python3
"""Compatibility wrapper for the package-native recommendation CLI."""

import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
load_dotenv()

from event_collector.cli.recommend_stock import main, parse_args, render_recommendation


if __name__ == "__main__":
    raise SystemExit(main())
