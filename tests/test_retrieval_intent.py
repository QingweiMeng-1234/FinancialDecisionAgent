from __future__ import annotations

import pytest

from event_collector.retrieval_intent import normalize_retrieval_intent


def test_normalize_retrieval_intent_accepts_detail_as_direct_alias():
    assert normalize_retrieval_intent("detail") == "direct"


def test_normalize_retrieval_intent_rejects_unknown_values():
    with pytest.raises(ValueError, match="retrieval_intent must be one of direct, indirect"):
        normalize_retrieval_intent("broad")
