"""Validation and rendering for retrieval proof attached to persisted assets."""

from __future__ import annotations

from collections.abc import Mapping


REQUIRED_PUBLICATION_PROVENANCE_FIELDS = (
    "generation_id",
    "corpus_snapshot_id",
    "index_config_fingerprint",
)

OPTIONAL_PUBLICATION_PROVENANCE_FIELDS = (
    "corpus_id",
    "collection_name",
    "embedding_artifact",
)


def require_publication_provenance(value: Mapping[str, object] | None) -> dict[str, str]:
    """Return the request-pinned retrieval proof or reject persistence.

    Asset writers must use this before opening directories or files.  A compact
    copy prevents a mutable caller-owned mapping from changing after validation.
    """
    if not isinstance(value, Mapping):
        raise ValueError("publication provenance must be a mapping with request-pinned retrieval proof")
    proof: dict[str, str] = {}
    for field in REQUIRED_PUBLICATION_PROVENANCE_FIELDS:
        raw_value = value.get(field)
        if not isinstance(raw_value, str) or not raw_value.strip():
            raise ValueError(f"publication provenance requires non-empty {field}")
        proof[field] = raw_value.strip()
    for field in OPTIONAL_PUBLICATION_PROVENANCE_FIELDS:
        if field not in value:
            continue
        raw_value = value[field]
        if not isinstance(raw_value, str) or not raw_value.strip():
            raise ValueError(f"publication provenance requires non-empty {field} when provided")
        proof[field] = raw_value.strip()
    return proof


def render_publication_provenance_markdown(proof: Mapping[str, object]) -> list[str]:
    """Render a validated proof as a stable Markdown section."""
    required = require_publication_provenance(proof)
    lines = [
        "## Retrieval Provenance",
        f"- Generation ID: {required['generation_id']}",
        f"- Corpus snapshot ID: {required['corpus_snapshot_id']}",
        f"- Index config fingerprint: {required['index_config_fingerprint']}",
    ]
    optional_labels = {
        "corpus_id": "Corpus ID",
        "collection_name": "Collection name",
        "embedding_artifact": "Embedding artifact",
    }
    for field in OPTIONAL_PUBLICATION_PROVENANCE_FIELDS:
        if field in required:
            lines.append(f"- {optional_labels[field]}: {required[field]}")
    return lines
