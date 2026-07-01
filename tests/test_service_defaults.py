from __future__ import annotations

import json

from event_collector.service_defaults import (
    DEFAULT_SERVICE_DEFAULTS_PATH,
    ServiceDefaults,
    load_service_defaults,
)


def test_load_service_defaults_uses_built_in_values_when_file_missing(tmp_path):
    defaults = load_service_defaults(str(tmp_path / "missing.json"))

    assert defaults == ServiceDefaults()


def test_load_service_defaults_reads_known_fields_and_ignores_invalid_values(tmp_path):
    path = tmp_path / "defaults.json"
    path.write_text(
        json.dumps(
            {
                "query_top_k": 7,
                "retrieval_top_k": 9,
                "recommendation_top_k": "bad",
                "watchlist_top_n": 4,
                "watchlist_retrieval_top_k": 6,
            }
        ),
        encoding="utf-8",
    )

    defaults = load_service_defaults(str(path))

    assert defaults.query_top_k == 7
    assert defaults.retrieval_top_k == 9
    assert defaults.recommendation_top_k == ServiceDefaults().recommendation_top_k
    assert defaults.watchlist_top_n == 4
    assert defaults.watchlist_retrieval_top_k == 6


def test_default_service_defaults_path_exists_in_repo():
    defaults = load_service_defaults(DEFAULT_SERVICE_DEFAULTS_PATH)

    assert defaults == ServiceDefaults(
        query_top_k=3,
        retrieval_top_k=5,
        recommendation_top_k=3,
        watchlist_top_n=3,
        watchlist_retrieval_top_k=5,
    )
