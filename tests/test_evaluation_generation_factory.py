from __future__ import annotations

from dataclasses import dataclass

import pytest

from event_collector.evaluation_generation_factory import (
    EvaluationCorpusConfig,
    open_evaluation_corpus,
)


@dataclass(frozen=True)
class _Snapshot:
    generation_id: str
    corpus_id: str = "news"


@dataclass(frozen=True)
class _Eligibility:
    active_generation: _Snapshot


class _Resolver:
    def __init__(self, calls):
        self.calls = calls

    def resolve(self, corpus_id):
        self.calls.append(("resolve", corpus_id))
        return _Snapshot("active-generation")

    def open_verified_candidate(self, generation_id):
        self.calls.append(("candidate", generation_id))
        return _Snapshot(generation_id)


def _config(generation_id=None):
    return EvaluationCorpusConfig(
        index_control_db_path="control.db",
        index_corpus_id="news",
        canonical_db_path="canonical.db",
        canonical_content_root="content",
        chroma_persist_dir="chroma",
        generation_id=generation_id,
    )


def test_active_mode_uses_pointer_and_never_opens_candidate() -> None:
    calls = []

    def active_builder(snapshot, database, root):
        calls.append(("active_eligibility", snapshot.generation_id, database, root))
        return _Eligibility(snapshot)

    result = open_evaluation_corpus(
        _config(),
        resolver_factory=lambda path: _Resolver(calls),
        active_eligibility_builder=active_builder,
        candidate_eligibility_builder=lambda *args: (_ for _ in ()).throw(
            AssertionError("candidate builder called")
        ),
        reader_factory=lambda eligibility, persist_dir: (eligibility, persist_dir),
    )

    assert result.resolution_mode == "active_pointer"
    assert result.generation.generation_id == "active-generation"
    assert result.reader == (result.eligibility, "chroma")
    assert calls == [
        ("resolve", "news"),
        ("active_eligibility", "active-generation", "canonical.db", "content"),
    ]


def test_candidate_mode_uses_explicit_verified_candidate_not_active_pointer() -> None:
    calls = []

    def candidate_builder(snapshot, database, root):
        calls.append(("candidate_eligibility", snapshot.generation_id, database, root))
        return _Eligibility(snapshot)

    result = open_evaluation_corpus(
        _config("candidate-7"),
        resolver_factory=lambda path: _Resolver(calls),
        active_eligibility_builder=lambda *args: (_ for _ in ()).throw(
            AssertionError("active builder called")
        ),
        candidate_eligibility_builder=candidate_builder,
        reader_factory=lambda eligibility, persist_dir: (eligibility, persist_dir),
    )

    assert result.resolution_mode == "verified_candidate"
    assert result.generation.generation_id == "candidate-7"
    assert calls == [
        ("candidate", "candidate-7"),
        ("candidate_eligibility", "candidate-7", "canonical.db", "content"),
    ]


def test_candidate_mode_rejects_verified_generation_from_another_corpus() -> None:
    class WrongCorpusResolver(_Resolver):
        def open_verified_candidate(self, generation_id):
            self.calls.append(("candidate", generation_id))
            return _Snapshot(generation_id, corpus_id="other-corpus")

    with pytest.raises(ValueError, match="does not belong"):
        open_evaluation_corpus(
            _config("candidate-7"),
            resolver_factory=lambda path: WrongCorpusResolver([]),
            reader_factory=lambda *args, **kwargs: None,
        )
