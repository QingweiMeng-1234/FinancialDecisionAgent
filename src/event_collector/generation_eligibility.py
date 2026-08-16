"""Fail-closed request snapshots for active-generation retrieval eligibility."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path, PurePosixPath, PureWindowsPath
import sqlite3
from typing import Iterable

from event_collector.active_generation_reader import (
    ActiveGenerationSnapshot,
    ActiveManifestEntry,
)


ELIGIBILITY_POLICY_VERSION = "active-generation-eligibility-v1"
VERIFIED_CANDIDATE_ELIGIBILITY_POLICY_VERSION = "verified-candidate-eligibility-v1"

MISSING_CANONICAL_ROW = "missing_canonical_row"
CONTENT_NOT_READY = "content_not_ready"
ARTICLE_QUARANTINED = "article_quarantined"
SOURCE_VALIDATION_INELIGIBLE = "source_validation_ineligible"
ACTIVE_HASH_MISMATCH = "active_hash_mismatch"
INVALID_CONTENT_PATH = "invalid_content_path"
CONTENT_FILE_MISSING = "content_file_missing"
CONTENT_FILE_NOT_REGULAR = "content_file_not_regular"
CONTENT_FILE_NOT_UTF8 = "content_file_not_utf8"
CONTENT_FILE_EMPTY = "content_file_empty"
CONTENT_FILE_HASH_MISMATCH = "content_file_hash_mismatch"

SOURCE_DATABASE_UNAVAILABLE = "source_database_unavailable"
CONTENT_ROOT_UNAVAILABLE = "content_root_unavailable"
CANONICAL_SCHEMA_INVALID = "canonical_schema_invalid"
CANONICAL_READ_FAILED = "canonical_read_failed"
ACTIVE_SNAPSHOT_INVALID = "active_snapshot_invalid"

_REQUIRED_COLUMNS = {
    "id",
    "source",
    "title",
    "published_at",
    "summary",
    "original_url",
    "canonical_url",
    "content_path",
    "content_status",
    "active_content_sha256",
    "summary_status",
    "summary_content_sha256",
    "content_validation_status",
    "quarantined_at",
}
_OPTIONAL_COLUMNS = {
    "content_validation_reason",
    "content_validator_version",
    "description",
    "url",
    "normalized_url",
    "publisher_source_id",
    "publisher_source_name",
    "story_group_id",
    "story_dedupe_method",
    "final_response_url",
    "response_status_code",
    "response_content_type",
    "extractor_version",
    "extracted_char_count",
    "fetched_at",
    "raw_json",
    "source_published_at",
    "published_at_provenance",
    "quarantine_reason",
}


class EligibilitySnapshotBuildError(RuntimeError):
    """The request-level eligibility snapshot cannot be built safely."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class EligibleGenerationArticle:
    """Canonical content and metadata frozen for one request."""

    article_id: int
    indexed_content_sha256: str
    content: str
    title: str
    description: str | None
    url: str | None
    canonical_url: str | None
    original_url: str | None
    normalized_url: str | None
    summary: str | None
    source: str
    published_at: str
    publisher_source_id: str | None = None
    publisher_source_name: str | None = None
    story_group_id: int | None = None
    story_dedupe_method: str | None = None
    content_validation_status: str | None = None
    content_validation_reason: str | None = None
    content_validator_version: str | None = None
    final_response_url: str | None = None
    response_status_code: int | None = None
    response_content_type: str | None = None
    extractor_version: str | None = None
    extracted_char_count: int | None = None
    fetched_at: str | None = None
    raw_json: str | None = None
    source_published_at: str | None = None
    published_at_provenance: str = "legacy_unverified"


@dataclass(frozen=True)
class EligibilityExclusion:
    """Stable per-article reason for exclusion from this request."""

    article_id: int
    code: str


@dataclass(frozen=True)
class EligibilitySnapshot:
    """Immutable active-generation/canonical intersection for one request."""

    active_generation: ActiveGenerationSnapshot
    generation_id: str
    corpus_id: str
    collection_name: str
    corpus_snapshot_id: str
    embedding_artifact: str
    index_config_fingerprint: str
    articles: tuple[EligibleGenerationArticle, ...]
    exclusions: tuple[EligibilityExclusion, ...]
    policy_version: str = ELIGIBILITY_POLICY_VERSION

    @property
    def eligible_article_ids(self) -> frozenset[int]:
        return frozenset(article.article_id for article in self.articles)


def intersect_allowed_article_ids(
    snapshot: EligibilitySnapshot,
    caller_allowed_ids: Iterable[int],
) -> frozenset[int]:
    """Intersect caller scope with the immutable generation-eligible set."""

    return snapshot.eligible_article_ids.intersection(caller_allowed_ids)


def build_eligibility_snapshot(
    active_generation: ActiveGenerationSnapshot,
    canonical_database: str | Path,
    content_root: str | Path,
) -> EligibilitySnapshot:
    """Build a request-pinned, fail-closed canonical view of one active generation."""

    return _build_generation_eligibility_snapshot(
        active_generation,
        canonical_database,
        content_root,
        required_status="active",
        policy_version=ELIGIBILITY_POLICY_VERSION,
    )


def build_verified_candidate_eligibility_snapshot(
    candidate_generation: ActiveGenerationSnapshot,
    canonical_database: str | Path,
    content_root: str | Path,
) -> EligibilitySnapshot:
    """Build an eval-only snapshot for one explicit verified candidate."""

    return _build_generation_eligibility_snapshot(
        candidate_generation,
        canonical_database,
        content_root,
        required_status="verified",
        policy_version=VERIFIED_CANDIDATE_ELIGIBILITY_POLICY_VERSION,
    )


def _build_generation_eligibility_snapshot(
    generation: ActiveGenerationSnapshot,
    canonical_database: str | Path,
    content_root: str | Path,
    *,
    required_status: str,
    policy_version: str,
) -> EligibilitySnapshot:

    database = _resolve_source_file(canonical_database)
    root = _resolve_content_root(content_root)
    manifest = _validate_active_snapshot(generation, required_status=required_status)

    connection = _readonly_connection(database)
    try:
        columns = _article_columns(connection)
        missing = sorted(_REQUIRED_COLUMNS.difference(columns))
        if missing:
            raise EligibilitySnapshotBuildError(
                CANONICAL_SCHEMA_INVALID,
                "canonical articles schema is missing required columns: " + ", ".join(missing),
            )
        selected_columns = sorted(_REQUIRED_COLUMNS | (_OPTIONAL_COLUMNS & columns))
        quoted = ", ".join(f'"{column}"' for column in selected_columns)
        rows = connection.execute(f"SELECT {quoted} FROM articles").fetchall()
    except EligibilitySnapshotBuildError:
        raise
    except sqlite3.Error as error:
        raise EligibilitySnapshotBuildError(
            CANONICAL_READ_FAILED,
            f"could not read canonical article metadata: {error}",
        ) from error
    finally:
        connection.close()

    by_id = {row["id"]: row for row in rows}
    articles: list[EligibleGenerationArticle] = []
    exclusions: list[EligibilityExclusion] = []
    for entry in manifest:
        row = by_id.get(entry.article_id)
        if row is None:
            exclusions.append(EligibilityExclusion(entry.article_id, MISSING_CANONICAL_ROW))
            continue
        exclusion = _metadata_exclusion(row, entry.indexed_content_sha256)
        if exclusion is not None:
            exclusions.append(EligibilityExclusion(entry.article_id, exclusion))
            continue
        content, content_exclusion = _read_content(
            root,
            row["content_path"],
            entry.indexed_content_sha256,
        )
        if content_exclusion is not None:
            exclusions.append(EligibilityExclusion(entry.article_id, content_exclusion))
            continue
        articles.append(_hydrate_article(row, entry.indexed_content_sha256, content))

    return EligibilitySnapshot(
        active_generation=generation,
        generation_id=generation.generation_id,
        corpus_id=generation.corpus_id,
        collection_name=generation.collection_name,
        corpus_snapshot_id=generation.corpus_snapshot_id,
        embedding_artifact=generation.embedding_artifact,
        index_config_fingerprint=generation.index_config_fingerprint,
        articles=tuple(articles),
        exclusions=tuple(exclusions),
        policy_version=policy_version,
    )


def _resolve_source_file(value: str | Path) -> Path:
    try:
        path = Path(value).resolve(strict=True)
    except (OSError, RuntimeError, TypeError) as error:
        raise EligibilitySnapshotBuildError(
            SOURCE_DATABASE_UNAVAILABLE, "canonical source database is unavailable"
        ) from error
    if not path.is_file():
        raise EligibilitySnapshotBuildError(
            SOURCE_DATABASE_UNAVAILABLE, "canonical source database is not a file"
        )
    return path


def _resolve_content_root(value: str | Path) -> Path:
    try:
        path = Path(value).resolve(strict=True)
    except (OSError, RuntimeError, TypeError) as error:
        raise EligibilitySnapshotBuildError(
            CONTENT_ROOT_UNAVAILABLE, "canonical content root is unavailable"
        ) from error
    if not path.is_dir():
        raise EligibilitySnapshotBuildError(
            CONTENT_ROOT_UNAVAILABLE, "canonical content root is not a directory"
        )
    return path


def _readonly_connection(database: Path) -> sqlite3.Connection:
    try:
        connection = sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        connection.execute("BEGIN")
        return connection
    except sqlite3.Error as error:
        raise EligibilitySnapshotBuildError(
            SOURCE_DATABASE_UNAVAILABLE, "could not open canonical source database read-only"
        ) from error


def _article_columns(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute("PRAGMA table_info(articles)").fetchall()
    if not rows:
        raise EligibilitySnapshotBuildError(
            CANONICAL_SCHEMA_INVALID, "canonical articles table is missing"
        )
    return {row["name"] for row in rows}


def _validate_active_snapshot(
    active_generation: ActiveGenerationSnapshot,
    *,
    required_status: str = "active",
) -> tuple[ActiveManifestEntry, ...]:
    if not isinstance(active_generation, ActiveGenerationSnapshot):
        raise EligibilitySnapshotBuildError(
            ACTIVE_SNAPSHOT_INVALID, "eligibility requires an ActiveGenerationSnapshot"
        )
    if active_generation.status != required_status:
        raise EligibilitySnapshotBuildError(
            ACTIVE_SNAPSHOT_INVALID,
            f"eligibility requires generation status {required_status}",
        )
    manifest = tuple(getattr(active_generation, "manifest", ()))
    article_ids = [getattr(entry, "article_id", None) for entry in manifest]
    if any(
        not isinstance(article_id, int)
        or isinstance(article_id, bool)
        or article_id <= 0
        for article_id in article_ids
    ):
        raise EligibilitySnapshotBuildError(
            ACTIVE_SNAPSHOT_INVALID, "active manifest article IDs must be positive integers"
        )
    if len(article_ids) != len(set(article_ids)):
        raise EligibilitySnapshotBuildError(
            ACTIVE_SNAPSHOT_INVALID, "active manifest article IDs must be unique"
        )
    if article_ids != sorted(article_ids):
        raise EligibilitySnapshotBuildError(
            ACTIVE_SNAPSHOT_INVALID, "active manifest article IDs must be sorted"
        )
    observed_chunks = active_generation.observed_chunk_count
    if (
        not isinstance(observed_chunks, int)
        or isinstance(observed_chunks, bool)
        or observed_chunks < 0
    ):
        raise EligibilitySnapshotBuildError(
            ACTIVE_SNAPSHOT_INVALID, "active generation observed chunk count is invalid"
        )
    manifest_chunks = 0
    for entry in manifest:
        digest = getattr(entry, "indexed_content_sha256", None)
        if not _is_sha256(digest):
            raise EligibilitySnapshotBuildError(
                ACTIVE_SNAPSHOT_INVALID, "active manifest contains an invalid content hash"
            )
        expected = entry.expected_chunk_count
        actual = entry.actual_chunk_count
        if (
            entry.generation_id != active_generation.generation_id
            or entry.index_config_fingerprint
            != active_generation.index_config_fingerprint
            or entry.index_status != "indexed"
            or not isinstance(expected, int)
            or isinstance(expected, bool)
            or expected <= 0
            or not isinstance(actual, int)
            or isinstance(actual, bool)
            or actual <= 0
            or expected != actual
        ):
            raise EligibilitySnapshotBuildError(
                ACTIVE_SNAPSHOT_INVALID, "active manifest is internally inconsistent"
            )
        manifest_chunks += actual
    if manifest_chunks != observed_chunks:
        raise EligibilitySnapshotBuildError(
            ACTIVE_SNAPSHOT_INVALID,
            "active manifest chunk count does not match active generation proof",
        )
    return manifest


def _metadata_exclusion(row: sqlite3.Row, indexed_hash: str) -> str | None:
    if row["content_status"] != "ready":
        return CONTENT_NOT_READY
    if row["quarantined_at"]:
        return ARTICLE_QUARANTINED
    source = row["source"]
    validation = row["content_validation_status"]
    if source == "news":
        if validation != "verified":
            return SOURCE_VALIDATION_INELIGIBLE
    elif validation not in {"verified", "not_applicable"}:
        return SOURCE_VALIDATION_INELIGIBLE
    if row["active_content_sha256"] != indexed_hash:
        return ACTIVE_HASH_MISMATCH
    return None


def _read_content(root: Path, raw_path, expected_hash: str) -> tuple[str, str | None]:
    if not isinstance(raw_path, str) or not raw_path.strip() or "\x00" in raw_path:
        return "", INVALID_CONTENT_PATH
    supplied = raw_path.strip()
    posix = PurePosixPath(supplied)
    windows = PureWindowsPath(supplied)
    if posix.is_absolute() or windows.is_absolute() or windows.drive:
        return "", INVALID_CONTENT_PATH
    if ".." in posix.parts or ".." in windows.parts:
        return "", INVALID_CONTENT_PATH
    candidate = root / Path(supplied)
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError:
        return "", CONTENT_FILE_MISSING
    except (OSError, RuntimeError):
        return "", INVALID_CONTENT_PATH
    try:
        resolved.relative_to(root)
    except ValueError:
        return "", INVALID_CONTENT_PATH
    if not resolved.is_file():
        return "", CONTENT_FILE_NOT_REGULAR
    try:
        content = resolved.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return "", CONTENT_FILE_NOT_UTF8
    except OSError:
        return "", CONTENT_FILE_MISSING
    if not content.strip():
        return "", CONTENT_FILE_EMPTY
    if hashlib.sha256(content.encode("utf-8")).hexdigest() != expected_hash:
        return "", CONTENT_FILE_HASH_MISMATCH
    return content, None


def _hydrate_article(
    row: sqlite3.Row,
    indexed_hash: str,
    content: str,
) -> EligibleGenerationArticle:
    summary = None
    candidate_summary = row["summary"]
    if (
        row["summary_status"] == "ready"
        and row["summary_content_sha256"] == indexed_hash
        and isinstance(candidate_summary, str)
        and candidate_summary.strip()
    ):
        summary = candidate_summary
    keys = set(row.keys())
    optional = lambda name: row[name] if name in keys else None
    return EligibleGenerationArticle(
        article_id=row["id"],
        indexed_content_sha256=indexed_hash,
        content=content,
        title=row["title"],
        description=optional("description"),
        url=optional("url"),
        canonical_url=row["canonical_url"],
        original_url=row["original_url"],
        normalized_url=optional("normalized_url"),
        summary=summary,
        source=row["source"],
        published_at=row["published_at"],
        publisher_source_id=optional("publisher_source_id"),
        publisher_source_name=optional("publisher_source_name"),
        story_group_id=optional("story_group_id"),
        story_dedupe_method=optional("story_dedupe_method"),
        content_validation_status=row["content_validation_status"],
        content_validation_reason=optional("content_validation_reason"),
        content_validator_version=optional("content_validator_version"),
        final_response_url=optional("final_response_url"),
        response_status_code=optional("response_status_code"),
        response_content_type=optional("response_content_type"),
        extractor_version=optional("extractor_version"),
        extracted_char_count=optional("extracted_char_count"),
        fetched_at=optional("fetched_at"),
        raw_json=optional("raw_json"),
        source_published_at=optional("source_published_at"),
        published_at_provenance=optional("published_at_provenance") or "legacy_unverified",
    )


def _is_sha256(value) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return value == value.lower()
