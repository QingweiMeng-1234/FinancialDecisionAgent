"""Read-only canonical-content input for immutable index generations.

This module freezes and manifests a generation input. It does not import an
index backend, mutate SQLite, or write article content. A row satisfying the
canonical eligibility metadata contract is validated fail-closed against its
stored relative path and active hash.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import sqlite3
from typing import Any, Iterable


MANIFEST_SCHEMA_VERSION = 1
_REQUIRED_COLUMNS = {
    "id",
    "source",
    "content_path",
    "active_content_sha256",
    "content_status",
    "content_validation_status",
    "quarantined_at",
}
_MANIFEST_KEYS = {
    "schema_version",
    "source_database_logical_sha256",
    "snapshot_id",
    "eligible_snapshot_fingerprint",
    "articles",
}
_ENTRY_KEYS = {"article_id", "content_sha256"}


class GenerationCorpusError(RuntimeError):
    """Canonical input cannot be proven safe and complete."""


@dataclass(frozen=True)
class CanonicalGenerationCandidate:
    """One immutable in-memory input for a generation builder."""

    article_id: int
    content: str
    content_sha256: str


@dataclass(frozen=True)
class GenerationManifestEntry:
    """Content-free identity persisted for one eligible article."""

    article_id: int
    content_sha256: str

    def to_dict(self) -> dict[str, object]:
        return {"article_id": self.article_id, "content_sha256": self.content_sha256}


@dataclass(frozen=True)
class GenerationInputManifest:
    """Portable identity of the exact canonical set supplied to a build."""

    entries: tuple[GenerationManifestEntry, ...]
    eligible_snapshot_fingerprint: str
    source_database_logical_sha256: str | None = None
    snapshot_id: str | None = None
    schema_version: int = MANIFEST_SCHEMA_VERSION

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "source_database_logical_sha256": self.source_database_logical_sha256,
            "snapshot_id": self.snapshot_id,
            "eligible_snapshot_fingerprint": self.eligible_snapshot_fingerprint,
            "articles": [entry.to_dict() for entry in self.entries],
        }

    def to_json(self) -> str:
        return _canonical_json(self.to_dict()) + "\n"


@dataclass(frozen=True)
class GenerationCorpusSnapshot:
    """Sorted immutable content inputs plus their deterministic set identity."""

    candidates: tuple[CanonicalGenerationCandidate, ...]
    eligible_snapshot_fingerprint: str
    source_database_logical_sha256: str | None = None
    snapshot_id: str | None = None

    def to_manifest(self) -> GenerationInputManifest:
        return GenerationInputManifest(
            entries=tuple(
                GenerationManifestEntry(candidate.article_id, candidate.content_sha256)
                for candidate in self.candidates
            ),
            eligible_snapshot_fingerprint=self.eligible_snapshot_fingerprint,
            source_database_logical_sha256=self.source_database_logical_sha256,
            snapshot_id=self.snapshot_id,
        )


FingerprintEntry = GenerationManifestEntry | CanonicalGenerationCandidate | tuple[int, str]


def compute_eligible_snapshot_fingerprint(entries: Iterable[FingerprintEntry]) -> str:
    """Hash compact UTF-8 JSON containing sorted article ID/hash pairs."""

    identities: dict[int, str] = {}
    for entry in entries:
        if isinstance(entry, (GenerationManifestEntry, CanonicalGenerationCandidate)):
            article_id = entry.article_id
            content_sha256 = entry.content_sha256
        else:
            try:
                article_id, content_sha256 = entry
            except (TypeError, ValueError) as error:
                raise GenerationCorpusError(
                    "fingerprint entries must contain article_id and content_sha256"
                ) from error
        _validate_article_id(article_id)
        _validate_sha256(content_sha256, "content_sha256")
        if article_id in identities:
            raise GenerationCorpusError(f"fingerprint article IDs must be unique: {article_id}")
        identities[article_id] = content_sha256
    payload = [[article_id, identities[article_id]] for article_id in sorted(identities)]
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def build_generation_corpus_snapshot(
    source_database: str | Path,
    content_root: str | Path,
    *,
    source_database_logical_sha256: str | None = None,
    snapshot_id: str | None = None,
) -> GenerationCorpusSnapshot:
    """Prove the complete canonical generation input without source mutation.

    Eligibility deliberately ignores old index_status. Ready, unquarantined
    news must be verified. Non-news may be verified or not_applicable. Every
    row satisfying those metadata predicates must pass path/content/hash
    validation or the whole snapshot is rejected.
    """

    database = _resolve_source_file(source_database, "source database")
    root = _resolve_source_directory(content_root, "content root")
    logical_identity = _optional_sha256(
        source_database_logical_sha256, "source_database_logical_sha256"
    )
    normalized_snapshot_id = _optional_identity(snapshot_id, "snapshot_id")

    connection = _readonly_connection(database)
    try:
        _require_article_columns(connection)
        rows = connection.execute(
            """
            SELECT id, source, content_path, active_content_sha256
            FROM articles
            WHERE content_status = 'ready'
              AND quarantined_at IS NULL
              AND active_content_sha256 IS NOT NULL
              AND length(trim(active_content_sha256)) > 0
              AND (
                    (source = 'news' AND content_validation_status = 'verified')
                 OR (source != 'news'
                     AND content_validation_status IN ('verified', 'not_applicable'))
              )
            ORDER BY id
            """
        ).fetchall()
    except sqlite3.Error as error:
        raise GenerationCorpusError(f"could not read canonical article metadata: {error}") from error
    finally:
        connection.close()

    candidates: list[CanonicalGenerationCandidate] = []
    previous_article_id: int | None = None
    for row in rows:
        article_id = row["id"]
        _validate_article_id(article_id)
        if previous_article_id == article_id:
            raise GenerationCorpusError(f"eligible article IDs must be unique: {article_id}")
        previous_article_id = article_id
        expected_hash = row["active_content_sha256"]
        _validate_sha256(expected_hash, f"article {article_id} active_content_sha256")
        content = _read_canonical_content(root, article_id, row["content_path"])
        observed_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if observed_hash != expected_hash:
            raise GenerationCorpusError(
                f"article {article_id} canonical content hash mismatch: "
                f"expected {expected_hash}, observed {observed_hash}"
            )
        candidates.append(CanonicalGenerationCandidate(article_id, content, expected_hash))

    frozen_candidates = tuple(candidates)
    return GenerationCorpusSnapshot(
        candidates=frozen_candidates,
        eligible_snapshot_fingerprint=compute_eligible_snapshot_fingerprint(frozen_candidates),
        source_database_logical_sha256=logical_identity,
        snapshot_id=normalized_snapshot_id,
    )


def write_generation_manifest(
    snapshot_or_manifest: GenerationCorpusSnapshot | GenerationInputManifest,
    path: str | Path,
) -> Path:
    """Exclusively create a content-free manifest; never overwrite."""

    if isinstance(snapshot_or_manifest, GenerationCorpusSnapshot):
        manifest = snapshot_or_manifest.to_manifest()
    elif isinstance(snapshot_or_manifest, GenerationInputManifest):
        manifest = snapshot_or_manifest
    else:
        raise TypeError("snapshot_or_manifest must be a generation snapshot or manifest")
    _validate_manifest_value(manifest)
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(manifest.to_json())
    return destination


def load_generation_manifest(path: str | Path) -> GenerationInputManifest:
    """Load a manifest and validate its canonical representation."""

    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise GenerationCorpusError(f"could not read generation manifest: {error}") from error
    if not isinstance(raw, dict):
        raise GenerationCorpusError("generation manifest must be a JSON object")
    if set(raw) != _MANIFEST_KEYS:
        raise GenerationCorpusError("generation manifest has missing or unexpected fields")
    if raw["schema_version"] != MANIFEST_SCHEMA_VERSION or isinstance(
        raw["schema_version"], bool
    ):
        raise GenerationCorpusError(
            f"unsupported generation manifest schema_version: {raw['schema_version']!r}"
        )
    logical_identity = _optional_sha256(
        raw["source_database_logical_sha256"], "source_database_logical_sha256"
    )
    normalized_snapshot_id = _optional_identity(raw["snapshot_id"], "snapshot_id")
    supplied_fingerprint = raw["eligible_snapshot_fingerprint"]
    _validate_sha256(supplied_fingerprint, "eligible_snapshot_fingerprint")
    articles = raw["articles"]
    if not isinstance(articles, list):
        raise GenerationCorpusError("generation manifest articles must be a list")

    entries: list[GenerationManifestEntry] = []
    for value in articles:
        if not isinstance(value, dict) or set(value) != _ENTRY_KEYS:
            raise GenerationCorpusError(
                "each manifest article must contain only article_id and content_sha256"
            )
        article_id = value["article_id"]
        content_sha256 = value["content_sha256"]
        _validate_article_id(article_id)
        _validate_sha256(content_sha256, f"article {article_id} content_sha256")
        entries.append(GenerationManifestEntry(article_id, content_sha256))

    article_ids = [entry.article_id for entry in entries]
    if len(article_ids) != len(set(article_ids)):
        raise GenerationCorpusError("manifest article IDs must be unique")
    if article_ids != sorted(article_ids):
        raise GenerationCorpusError("manifest article IDs must be sorted ascending")
    expected_fingerprint = compute_eligible_snapshot_fingerprint(entries)
    if supplied_fingerprint != expected_fingerprint:
        raise GenerationCorpusError(
            "eligible snapshot fingerprint does not match manifest articles"
        )
    manifest = GenerationInputManifest(
        entries=tuple(entries),
        eligible_snapshot_fingerprint=supplied_fingerprint,
        source_database_logical_sha256=logical_identity,
        snapshot_id=normalized_snapshot_id,
    )
    _validate_manifest_value(manifest)
    return manifest


def _validate_manifest_value(manifest: GenerationInputManifest) -> None:
    if manifest.schema_version != MANIFEST_SCHEMA_VERSION:
        raise GenerationCorpusError("generation manifest schema_version is unsupported")
    _optional_sha256(
        manifest.source_database_logical_sha256, "source_database_logical_sha256"
    )
    _optional_identity(manifest.snapshot_id, "snapshot_id")
    article_ids = [entry.article_id for entry in manifest.entries]
    if len(article_ids) != len(set(article_ids)):
        raise GenerationCorpusError("manifest article IDs must be unique")
    if article_ids != sorted(article_ids):
        raise GenerationCorpusError("manifest article IDs must be sorted ascending")
    for entry in manifest.entries:
        if not isinstance(entry, GenerationManifestEntry):
            raise GenerationCorpusError(
                "manifest entries must be GenerationManifestEntry values"
            )
        _validate_article_id(entry.article_id)
        _validate_sha256(entry.content_sha256, f"article {entry.article_id} content_sha256")
    expected = compute_eligible_snapshot_fingerprint(manifest.entries)
    if manifest.eligible_snapshot_fingerprint != expected:
        raise GenerationCorpusError(
            "eligible snapshot fingerprint does not match manifest articles"
        )


def _readonly_connection(path: Path) -> sqlite3.Connection:
    try:
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        return connection
    except sqlite3.Error as error:
        raise GenerationCorpusError(
            f"could not open source database read-only: {error}"
        ) from error


def _require_article_columns(connection: sqlite3.Connection) -> None:
    try:
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(articles)").fetchall()
        }
    except sqlite3.Error as error:
        raise GenerationCorpusError(f"could not inspect articles schema: {error}") from error
    missing = sorted(_REQUIRED_COLUMNS - columns)
    if missing:
        raise GenerationCorpusError(
            "articles table missing required canonical columns: " + ", ".join(missing)
        )


def _read_canonical_content(root: Path, article_id: int, stored_path: object) -> str:
    if not isinstance(stored_path, str) or not stored_path.strip():
        raise GenerationCorpusError(
            f"article {article_id} has no canonical relative content path"
        )
    if "\x00" in stored_path:
        raise GenerationCorpusError(
            f"article {article_id} has an invalid canonical content path"
        )
    posix_path = PurePosixPath(stored_path)
    windows_path = PureWindowsPath(stored_path)
    if posix_path.is_absolute() or windows_path.is_absolute() or windows_path.drive:
        raise GenerationCorpusError(
            f"article {article_id} canonical content path must be relative"
        )
    if ".." in posix_path.parts or ".." in windows_path.parts:
        raise GenerationCorpusError(
            f"article {article_id} canonical content path contains traversal"
        )
    try:
        candidate = (root / stored_path).resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise GenerationCorpusError(
            f"article {article_id} canonical content file is missing or unreadable"
        ) from error
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise GenerationCorpusError(
            f"article {article_id} canonical content path escapes the supplied content root"
        ) from error
    if not candidate.is_file():
        raise GenerationCorpusError(
            f"article {article_id} canonical content path is not a file"
        )
    try:
        content = candidate.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise GenerationCorpusError(
            f"article {article_id} canonical content is unreadable UTF-8"
        ) from error
    if not content.strip():
        raise GenerationCorpusError(f"article {article_id} canonical content is empty")
    return content


def _resolve_source_file(value: str | Path, label: str) -> Path:
    try:
        resolved = Path(value).resolve(strict=True)
    except (OSError, RuntimeError, TypeError) as error:
        raise GenerationCorpusError(f"{label} does not exist") from error
    if not resolved.is_file():
        raise GenerationCorpusError(f"{label} is not a file")
    return resolved


def _resolve_source_directory(value: str | Path, label: str) -> Path:
    try:
        resolved = Path(value).resolve(strict=True)
    except (OSError, RuntimeError, TypeError) as error:
        raise GenerationCorpusError(f"{label} does not exist") from error
    if not resolved.is_dir():
        raise GenerationCorpusError(f"{label} is not a directory")
    return resolved


def _validate_article_id(value: object) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise GenerationCorpusError(
            f"article_id must be a positive integer: {value!r}"
        )


def _validate_sha256(value: object, label: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise GenerationCorpusError(
            f"{label} must be a lowercase hexadecimal SHA-256"
        )


def _optional_sha256(value: object, label: str) -> str | None:
    if value is None:
        return None
    _validate_sha256(value, label)
    return value


def _optional_identity(value: object, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise GenerationCorpusError(
            f"{label} must be a non-empty string when supplied"
        )
    return value


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
