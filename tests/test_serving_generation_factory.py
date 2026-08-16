from __future__ import annotations

from dataclasses import dataclass
import sqlite3

import pytest

from event_collector.active_generation_reader import ActiveGenerationResolutionError
from event_collector.generation_eligibility import EligibilitySnapshotBuildError
from event_collector.serving_generation_factory import (
    CORPUS_UNAVAILABLE,
    CorpusUnavailableError,
    ServingCorpusConfig,
    create_active_generation_reader,
)


@dataclass(frozen=True)
class _Snapshot:
    generation_id: str = "generation-1"


@dataclass(frozen=True)
class _Eligibility:
    active_generation: _Snapshot


class _Resolver:
    def __init__(self, calls: list[tuple], *, error: Exception | None = None) -> None:
        self.calls = calls
        self.error = error

    def resolve(self, corpus_id: str) -> _Snapshot:
        self.calls.append(("resolve", corpus_id))
        if self.error is not None:
            raise self.error
        return _Snapshot()


def _config() -> ServingCorpusConfig:
    return ServingCorpusConfig(
        index_control_db_path="control.db",
        index_corpus_id="news-v2",
        canonical_db_path="canonical.db",
        canonical_content_root="canonical-content",
        chroma_persist_dir="chroma-v2",
    )


def test_factory_resolves_active_then_builds_eligibility_then_opens_reader() -> None:
    calls: list[tuple] = []
    resolver = _Resolver(calls)

    def build_eligibility(active, database, content_root):
        calls.append(("eligibility", active, database, content_root))
        return _Eligibility(active)

    reader = object()

    def open_reader(eligibility, *, persist_dir):
        calls.append(("reader", eligibility, persist_dir))
        return reader

    serving = create_active_generation_reader(
        _config(),
        resolver_factory=lambda path: resolver,
        eligibility_builder=build_eligibility,
        reader_factory=open_reader,
    )

    assert serving.reader is reader
    assert serving.active_generation.generation_id == "generation-1"
    assert serving.eligibility.active_generation is serving.active_generation
    assert calls == [
        ("resolve", "news-v2"),
        ("eligibility", serving.active_generation, "canonical.db", "canonical-content"),
        ("reader", serving.eligibility, "chroma-v2"),
    ]


def test_resolution_failure_is_stable_and_never_opens_canonical_or_chroma() -> None:
    calls: list[tuple] = []
    resolver = _Resolver(
        calls,
        error=ActiveGenerationResolutionError("missing_pointer", "not active"),
    )

    with pytest.raises(CorpusUnavailableError) as raised:
        create_active_generation_reader(
            _config(),
            resolver_factory=lambda path: resolver,
            eligibility_builder=lambda *args: pytest.fail("canonical must stay closed"),
            reader_factory=lambda *args, **kwargs: pytest.fail("Chroma must stay closed"),
        )

    assert raised.value.code == CORPUS_UNAVAILABLE
    assert raised.value.reason_code == "missing_pointer"
    assert raised.value.stage == "active_generation"
    assert calls == [("resolve", "news-v2")]


def test_unreadable_control_database_is_stable_and_never_opens_downstream() -> None:
    resolver = _Resolver([], error=sqlite3.OperationalError("unable to open database"))

    with pytest.raises(CorpusUnavailableError) as raised:
        create_active_generation_reader(
            _config(),
            resolver_factory=lambda path: resolver,
            eligibility_builder=lambda *args: pytest.fail("canonical must stay closed"),
            reader_factory=lambda *args, **kwargs: pytest.fail("Chroma must stay closed"),
        )

    assert raised.value.reason_code == "control_plane_unavailable"
    assert raised.value.stage == "active_generation"


def test_eligibility_failure_never_opens_chroma() -> None:
    calls: list[tuple] = []
    resolver = _Resolver(calls)

    def fail_eligibility(*args):
        calls.append(("eligibility",))
        raise EligibilitySnapshotBuildError("content_root_unavailable", "missing")

    with pytest.raises(CorpusUnavailableError) as raised:
        create_active_generation_reader(
            _config(),
            resolver_factory=lambda path: resolver,
            eligibility_builder=fail_eligibility,
            reader_factory=lambda *args, **kwargs: pytest.fail("Chroma must stay closed"),
        )

    assert raised.value.code == CORPUS_UNAVAILABLE
    assert raised.value.reason_code == "content_root_unavailable"
    assert raised.value.stage == "eligibility"
    assert calls == [("resolve", "news-v2"), ("eligibility",)]


def test_reader_contract_failure_is_reported_without_legacy_fallback() -> None:
    from event_collector.chroma_generation_reader import GenerationReaderContractError

    resolver = _Resolver([])

    with pytest.raises(CorpusUnavailableError) as raised:
        create_active_generation_reader(
            _config(),
            resolver_factory=lambda path: resolver,
            eligibility_builder=lambda active, database, root: _Eligibility(active),
            reader_factory=lambda *args, **kwargs: (_ for _ in ()).throw(
                GenerationReaderContractError("metadata mismatch")
            ),
        )

    assert raised.value.code == CORPUS_UNAVAILABLE
    assert raised.value.reason_code == "reader_contract_invalid"
    assert raised.value.stage == "generation_reader"


def test_reader_open_failure_is_stable_and_has_no_legacy_fallback() -> None:
    resolver = _Resolver([])

    with pytest.raises(CorpusUnavailableError) as raised:
        create_active_generation_reader(
            _config(),
            resolver_factory=lambda path: resolver,
            eligibility_builder=lambda active, database, root: _Eligibility(active),
            reader_factory=lambda *args, **kwargs: (_ for _ in ()).throw(
                OSError("persist root unavailable")
            ),
        )

    assert raised.value.reason_code == "reader_unavailable"
    assert raised.value.stage == "generation_reader"
