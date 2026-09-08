"""Deterministic, fail-closed Entity KB v2 mention resolution."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable


_TARGET_ENTITY_TYPES = {"company", "fund", "index"}

class EntityResolutionError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class EntityFact:
    entity_id: str
    entity_type: str
    canonical_name: str
    primary_symbol: str | None
    jurisdiction: str | None = None
    status: str = "active"


@dataclass(frozen=True)
class AliasFact:
    alias_id: str
    entity_id: str
    value: str
    strength_class: str
    ambiguity_class: str
    alias_type: str = "common_name"
    language: str = "en"


@dataclass(frozen=True)
class RelationshipFact:
    relationship_id: str
    subject_entity_id: str
    relation_type: str
    object_entity_id: str


@dataclass(frozen=True)
class AliasMatch:
    alias_id: str
    entity_id: str
    mention_text: str
    mention_start: int
    mention_end: int
    strength_class: str
    ambiguity_class: str


@dataclass(frozen=True)
class ResolutionDecision:
    status: str
    selected_entity: EntityFact | None
    candidate_entities: tuple[EntityFact, ...]
    alias_matches: tuple[AliasMatch, ...]
    reason_codes: tuple[str, ...]


def resolve_entities(
    query: str,
    entities: Iterable[EntityFact],
    aliases: Iterable[AliasFact],
    relationships: Iterable[RelationshipFact],
    *,
    target_entity_id: str | None = None,
) -> ResolutionDecision:
    if not isinstance(query, str) or not query.strip():
        raise EntityResolutionError("INVALID_ARGUMENT", "query must be non-empty")
    entity_by_id = {entity.entity_id: entity for entity in entities}
    alias_facts = tuple(aliases)
    relationship_facts = tuple(relationships)

    ticker_matches = tuple(
        entity
        for entity in entity_by_id.values()
        if entity.primary_symbol and _phrase_span(query, entity.primary_symbol, ticker=True)
    )
    alias_matches = _matched_aliases(query, alias_facts)
    strong_alias_entities = _ordered_entities(
        entity_by_id,
        (
            match.entity_id
            for match in alias_matches
            if _alias_by_id(alias_facts, match.alias_id).strength_class == "strong"
            and entity_by_id.get(match.entity_id)
            and entity_by_id[match.entity_id].entity_type in _TARGET_ENTITY_TYPES
        ),
    )
    canonical_matches = tuple(
        entity
        for entity in entity_by_id.values()
        if entity.entity_type in _TARGET_ENTITY_TYPES
        and _phrase_span(query, entity.canonical_name) is not None
    )

    evidence_entities = _ordered_unique_entities(
        (*ticker_matches, *strong_alias_entities, *canonical_matches)
    )
    if target_entity_id is not None:
        target = entity_by_id.get(target_entity_id)
        if target is None:
            raise EntityResolutionError("TARGET_ENTITY_MISMATCH", "target is absent from snapshot")
        conflicting = [
            entity
            for entity in evidence_entities
            if entity.entity_id != target_entity_id
        ]
        if conflicting:
            raise EntityResolutionError(
                "TARGET_ENTITY_MISMATCH",
                "explicit target conflicts with deterministic query evidence",
            )
        if evidence_entities:
            return ResolutionDecision(
                status="resolved",
                selected_entity=target,
                candidate_entities=(),
                alias_matches=alias_matches,
                reason_codes=("TARGET_OVERRIDE_VALIDATED",),
            )

    if len(ticker_matches) == 1:
        reasons = ["EXACT_TICKER"]
        if ticker_matches[0] in canonical_matches:
            reasons.append("CANONICAL_NAME")
        return _resolved(ticker_matches[0], alias_matches, reasons)

    unique_alias_entities = tuple(
        entity
        for entity in strong_alias_entities
        if any(
            match.entity_id == entity.entity_id
            and _alias_by_id(alias_facts, match.alias_id).ambiguity_class == "unique"
            for match in alias_matches
        )
    )
    ambiguous_alias_entities = tuple(
        entity
        for entity in strong_alias_entities
        if any(
            match.entity_id == entity.entity_id
            and _alias_by_id(alias_facts, match.alias_id).ambiguity_class != "unique"
            for match in alias_matches
        )
    )
    if len(unique_alias_entities) == 1 and not ambiguous_alias_entities:
        reasons = ["STRONG_ALIAS"]
        if unique_alias_entities[0] in canonical_matches:
            reasons.append("CANONICAL_NAME")
        return _resolved(unique_alias_entities[0], alias_matches, reasons)
    if len(strong_alias_entities) > 1 or ambiguous_alias_entities:
        return ResolutionDecision(
            status="ambiguous",
            selected_entity=None,
            candidate_entities=strong_alias_entities,
            alias_matches=alias_matches,
            reason_codes=("MULTIPLE_STRONG_MATCHES",),
        )
    if len(canonical_matches) == 1:
        return _resolved(canonical_matches[0], alias_matches, ("CANONICAL_NAME",))

    product_ids = {
        match.entity_id
        for match in alias_matches
        if entity_by_id.get(match.entity_id)
        and entity_by_id[match.entity_id].entity_type == "product"
    }
    owners = _ordered_entities(
        entity_by_id,
        (
            relationship.subject_entity_id
            for relationship in relationship_facts
            if relationship.relation_type == "owns_product"
            and relationship.object_entity_id in product_ids
        ),
    )
    if owners:
        return ResolutionDecision(
            status="unresolved",
            selected_entity=None,
            candidate_entities=owners,
            alias_matches=alias_matches,
            reason_codes=("PRODUCT_OWNER_CANDIDATE_ONLY",),
        )
    return ResolutionDecision(
        status="unresolved",
        selected_entity=None,
        candidate_entities=(),
        alias_matches=alias_matches,
        reason_codes=("NO_MATCH",),
    )


def _resolved(
    entity: EntityFact,
    alias_matches: tuple[AliasMatch, ...],
    reasons: Iterable[str],
) -> ResolutionDecision:
    return ResolutionDecision(
        status="resolved",
        selected_entity=entity,
        candidate_entities=(),
        alias_matches=alias_matches,
        reason_codes=tuple(reasons),
    )


def _matched_aliases(query: str, aliases: tuple[AliasFact, ...]) -> tuple[AliasMatch, ...]:
    matches = []
    for alias in aliases:
        span = _phrase_span(query, alias.value, ticker=alias.alias_type == "ticker")
        if span is None:
            continue
        start, end = span
        matches.append(
            AliasMatch(
                alias_id=alias.alias_id,
                entity_id=alias.entity_id,
                mention_text=query[start:end],
                mention_start=start,
                mention_end=end,
                strength_class=alias.strength_class,
                ambiguity_class=alias.ambiguity_class,
            )
        )
    return tuple(sorted(matches, key=lambda item: (item.mention_start, item.alias_id)))


def _phrase_span(query: str, phrase: str, *, ticker: bool = False) -> tuple[int, int] | None:
    escaped = re.escape(phrase)
    if phrase.isascii():
        pattern = rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])"
    else:
        pattern = escaped
    flags = 0 if ticker else re.IGNORECASE
    match = re.search(pattern, query, flags)
    return None if match is None else match.span()


def _ordered_entities(
    entity_by_id: dict[str, EntityFact],
    entity_ids: Iterable[str],
) -> tuple[EntityFact, ...]:
    return tuple(
        entity_by_id[entity_id]
        for entity_id in sorted(set(entity_ids))
        if entity_id in entity_by_id
    )


def _ordered_unique_entities(entities: Iterable[EntityFact]) -> tuple[EntityFact, ...]:
    return tuple({entity.entity_id: entity for entity in entities}.values())


def _alias_by_id(aliases: tuple[AliasFact, ...], alias_id: str) -> AliasFact:
    return next(alias for alias in aliases if alias.alias_id == alias_id)


__all__ = [
    "AliasFact",
    "AliasMatch",
    "EntityFact",
    "EntityResolutionError",
    "RelationshipFact",
    "ResolutionDecision",
    "resolve_entities",
]
