#!/usr/bin/env python3
"""Sync company-centric entity KB profiles into SQLite."""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from event_collector.entity_kb import (
    AutoCompanyProfileSource,
    CompanyProfile,
    CompanyProfileSource,
    SQLiteEntityStore,
    YamlSeedCompanyProfileSource,
    load_tickers_from_annotation_dir,
    save_company_profiles_seed,
    sync_ticker_universe,
)


class HybridCompanyProfileSource:
    def __init__(self, primary: CompanyProfileSource, fallback: CompanyProfileSource):
        self.primary = primary
        self.fallback = fallback
        self.resolved_profiles: dict[str, CompanyProfile] = {}

    def fetch_profile(self, ticker: str) -> CompanyProfile | None:
        profile = self.primary.fetch_profile(ticker)
        if profile is None:
            profile = self.fallback.fetch_profile(ticker)
        if profile is not None:
            self.resolved_profiles[profile.ticker] = profile
        return profile


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Sync company entity KB profiles from YAML seeds.")
    parser.add_argument(
        "--annotation-dir",
        default=os.path.join("annotations", "retrieval_eval_by_ticker"),
        help="Directory whose YAML filenames define the ticker universe",
    )
    parser.add_argument(
        "--seed-path",
        default=os.path.join("data", "company_entities", "company_profiles.yaml"),
        help="YAML file containing company profile seeds",
    )
    parser.add_argument(
        "--db-path",
        default="company_entities.db",
        help="SQLite path for the company entity KB",
    )
    parser.add_argument(
        "--mode",
        choices=("yaml", "auto", "hybrid"),
        default="hybrid",
        help="Profile source mode: curated YAML only, auto bootstrap only, or YAML with auto fallback",
    )
    parser.add_argument(
        "--write-resolved-seeds",
        action="store_true",
        help="Write successfully resolved profiles back to the seed YAML path",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    store = SQLiteEntityStore(db_path=args.db_path)
    store.init_db()
    try:
        yaml_source = YamlSeedCompanyProfileSource(args.seed_path)
        auto_source = AutoCompanyProfileSource()
        if args.mode == "yaml":
            source = HybridCompanyProfileSource(yaml_source, StaticNullProfileSource())
        elif args.mode == "auto":
            source = HybridCompanyProfileSource(auto_source, StaticNullProfileSource())
        else:
            source = HybridCompanyProfileSource(yaml_source, auto_source)
        results = sync_ticker_universe(args.annotation_dir, store, source)
        if args.write_resolved_seeds:
            ordered_profiles = [
                source.resolved_profiles[ticker]
                for ticker in load_tickers_from_annotation_dir(args.annotation_dir)
                if ticker in source.resolved_profiles
            ]
            save_company_profiles_seed(ordered_profiles, args.seed_path)
    finally:
        store.close()

    print("Company entity sync results:")
    for result in results:
        suffix = f" ({result.error})" if result.error else ""
        print(f"- {result.ticker}: {result.status}{suffix}")


class StaticNullProfileSource:
    def fetch_profile(self, ticker: str) -> CompanyProfile | None:
        return None


if __name__ == "__main__":
    main()
