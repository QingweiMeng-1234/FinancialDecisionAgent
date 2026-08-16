"""Fail-closed construction of request-pinned RAG serving readers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3
from typing import Any, Callable

from event_collector.active_generation_reader import (
    ActiveGenerationResolutionError,
    ActiveGenerationResolver,
    ActiveGenerationSnapshot,
)
from event_collector.chroma_generation_reader import (
    ChromaGenerationReader,
    GenerationReaderContractError,
)
from event_collector.generation_eligibility import (
    EligibilitySnapshot,
    EligibilitySnapshotBuildError,
    build_eligibility_snapshot,
)


CORPUS_UNAVAILABLE = "CORPUS_UNAVAILABLE"


class CorpusUnavailableError(RuntimeError):
    """A stable serving-boundary failure with no legacy retrieval fallback."""

    def __init__(self, *, stage: str, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.code = CORPUS_UNAVAILABLE
        self.stage = stage
        self.reason_code = reason_code


@dataclass(frozen=True)
class ServingCorpusConfig:
    """Operator-owned locations needed to resolve one serving corpus."""

    index_control_db_path: str | Path
    index_corpus_id: str
    canonical_db_path: str | Path
    canonical_content_root: str | Path
    chroma_persist_dir: str | Path


@dataclass(frozen=True)
class PinnedGenerationReader:
    """One immutable generation boundary shared for the lifetime of a request."""

    reader: ChromaGenerationReader
    active_generation: ActiveGenerationSnapshot
    eligibility: EligibilitySnapshot


def create_active_generation_reader(
    config: ServingCorpusConfig,
    *,
    resolver_factory: Callable[[str | Path], Any] = ActiveGenerationResolver,
    eligibility_builder: Callable[..., Any] = build_eligibility_snapshot,
    reader_factory: Callable[..., Any] = ChromaGenerationReader,
) -> PinnedGenerationReader:
    """Resolve, hydrate, and open exactly one active generation in that order."""

    resolver = resolver_factory(config.index_control_db_path)
    try:
        active_generation = resolver.resolve(config.index_corpus_id)
    except ActiveGenerationResolutionError as error:
        raise CorpusUnavailableError(
            stage="active_generation",
            reason_code=error.code,
            message=str(error),
        ) from error
    except (OSError, sqlite3.Error) as error:
        raise CorpusUnavailableError(
            stage="active_generation",
            reason_code="control_plane_unavailable",
            message=str(error) or "generation control plane is unavailable",
        ) from error

    try:
        eligibility = eligibility_builder(
            active_generation,
            config.canonical_db_path,
            config.canonical_content_root,
        )
    except EligibilitySnapshotBuildError as error:
        raise CorpusUnavailableError(
            stage="eligibility",
            reason_code=error.code,
            message=str(error),
        ) from error

    try:
        reader = reader_factory(
            eligibility,
            persist_dir=config.chroma_persist_dir,
        )
    except GenerationReaderContractError as error:
        raise CorpusUnavailableError(
            stage="generation_reader",
            reason_code="reader_contract_invalid",
            message=str(error),
        ) from error
    except Exception as error:
        raise CorpusUnavailableError(
            stage="generation_reader",
            reason_code="reader_unavailable",
            message=str(error) or "active generation reader is unavailable",
        ) from error

    return PinnedGenerationReader(
        reader=reader,
        active_generation=active_generation,
        eligibility=eligibility,
    )
