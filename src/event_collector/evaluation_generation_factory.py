"""Read-only generation selection for reproducible retrieval evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from event_collector.active_generation_reader import ActiveGenerationResolver
from event_collector.chroma_generation_reader import ChromaGenerationReader
from event_collector import generation_eligibility


@dataclass(frozen=True)
class EvaluationCorpusConfig:
    index_control_db_path: str | Path
    index_corpus_id: str
    canonical_db_path: str | Path
    canonical_content_root: str | Path
    chroma_persist_dir: str | Path
    generation_id: str | None = None


@dataclass(frozen=True)
class PinnedEvaluationCorpus:
    reader: Any
    generation: Any
    eligibility: Any
    resolution_mode: str


def open_evaluation_corpus(
    config: EvaluationCorpusConfig,
    *,
    resolver_factory: Callable[[str | Path], Any] = ActiveGenerationResolver,
    active_eligibility_builder: Callable[..., Any] | None = None,
    candidate_eligibility_builder: Callable[..., Any] | None = None,
    reader_factory: Callable[..., Any] = ChromaGenerationReader,
) -> PinnedEvaluationCorpus:
    """Pin either the active corpus or one explicit verified candidate."""

    resolver = resolver_factory(config.index_control_db_path)
    if config.generation_id is None:
        generation = resolver.resolve(config.index_corpus_id)
        builder = (
            active_eligibility_builder
            or generation_eligibility.build_eligibility_snapshot
        )
        resolution_mode = "active_pointer"
    else:
        generation = resolver.open_verified_candidate(config.generation_id)
        if generation.corpus_id != config.index_corpus_id:
            raise ValueError(
                "explicit verified generation does not belong to the requested evaluation corpus"
            )
        builder = candidate_eligibility_builder or getattr(
            generation_eligibility,
            "build_verified_candidate_eligibility_snapshot",
        )
        resolution_mode = "verified_candidate"

    eligibility = builder(
        generation,
        config.canonical_db_path,
        config.canonical_content_root,
    )
    reader = reader_factory(eligibility, persist_dir=config.chroma_persist_dir)
    return PinnedEvaluationCorpus(
        reader=reader,
        generation=generation,
        eligibility=eligibility,
        resolution_mode=resolution_mode,
    )
