"""Deterministic bounded query expansion from one resolved KB target."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from event_collector.entity_resolution import (
    AliasFact,
    EntityFact,
    RelationshipFact,
    ResolutionDecision,
)


class ExpansionError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ExpansionTerm:
    term: str
    source_type: str
    source_id: str
    priority: int


@dataclass(frozen=True)
class ExcludedTerm:
    term: str
    reason_code: str


@dataclass(frozen=True)
class QueryExpansion:
    original_query: str
    effective_query: str
    retrieval_intent: str
    included_terms: tuple[ExpansionTerm, ...]
    excluded_terms: tuple[ExcludedTerm, ...]
    relationship_hops: int
    term_limit: int


def build_query_expansion(
    query: str,
    decision: ResolutionDecision,
    *,
    entities: Iterable[EntityFact],
    aliases: Iterable[AliasFact],
    relationships: Iterable[RelationshipFact],
    retrieval_intent: str,
) -> QueryExpansion:
    if decision.status != "resolved" or decision.selected_entity is None:
        raise ExpansionError("UNRESOLVED_TARGET", "target-specific expansion needs resolution")
    if retrieval_intent not in {"direct", "indirect"}:
        raise ExpansionError("INVALID_ARGUMENT", "unknown retrieval intent")
    entity_by_id = {entity.entity_id: entity for entity in entities}
    aliases_by_entity: dict[str, list[AliasFact]] = {}
    for alias in aliases:
        aliases_by_entity.setdefault(alias.entity_id, []).append(alias)
    target = decision.selected_entity
    candidates: list[tuple[str, str, str]] = []
    if target.primary_symbol:
        candidates.append((target.primary_symbol, "primary_symbol", target.entity_id))
    candidates.append((target.canonical_name, "canonical_name", target.entity_id))
    for alias in sorted(
        aliases_by_entity.get(target.entity_id, ()),
        key=lambda item: item.alias_id,
    ):
        if alias.language.startswith("en"):
            candidates.append((alias.value, "alias", alias.alias_id))

    hops = 0
    relationship_facts = tuple(relationships)
    if retrieval_intent == "direct":
        related_ids = [
            relationship.object_entity_id
            for relationship in relationship_facts
            if relationship.subject_entity_id == target.entity_id
            and relationship.relation_type == "owns_product"
        ]
        term_limit = 8
    else:
        related_ids = [
            relationship.object_entity_id
            for relationship in relationship_facts
            if relationship.subject_entity_id == target.entity_id
            and relationship.relation_type
            in {"supplies_to", "customer_of", "depends_on", "competes_with", "substitutes_for"}
        ]
        hops = 1 if related_ids else 0
        term_limit = 12
    for entity_id in sorted(set(related_ids)):
        entity = entity_by_id.get(entity_id)
        if entity is not None:
            candidates.append((entity.canonical_name, "relationship_entity", entity.entity_id))
        for alias in sorted(aliases_by_entity.get(entity_id, ()), key=lambda item: item.alias_id):
            if alias.language.startswith("en"):
                candidates.append((alias.value, "relationship_alias", alias.alias_id))

    included = []
    excluded = []
    seen = set()
    for term, source_type, source_id in candidates:
        normalized = term.casefold().strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        if len(included) >= term_limit:
            excluded.append(ExcludedTerm(term, "TERM_BUDGET_EXCEEDED"))
            continue
        included.append(ExpansionTerm(term, source_type, source_id, len(included) + 1))
    effective = " ".join((query.strip(), *(term.term for term in included)))
    return QueryExpansion(
        original_query=query,
        effective_query=effective,
        retrieval_intent=retrieval_intent,
        included_terms=tuple(included),
        excluded_terms=tuple(excluded),
        relationship_hops=hops,
        term_limit=term_limit,
    )


__all__ = [
    "ExcludedTerm",
    "ExpansionError",
    "ExpansionTerm",
    "QueryExpansion",
    "build_query_expansion",
]
