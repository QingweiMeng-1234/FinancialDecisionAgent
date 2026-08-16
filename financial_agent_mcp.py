#!/usr/bin/env python3
"""Compatibility wrapper for the package-native MCP CLI."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from event_collector.cli.financial_agent_mcp import main, parse_args


if __name__ == "__main__":
    raise SystemExit(main())
