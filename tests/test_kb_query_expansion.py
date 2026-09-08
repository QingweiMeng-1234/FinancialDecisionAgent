from pathlib import Path
import sys
from types import ModuleType

import pytest


ROOT = Path(__file__).resolve().parents[1]
package = ModuleType("event_collector")
package.__path__ = [str(ROOT / "src" / "event_collector")]
sys.modules.setdefault("event_collector", package)

from event_collector.entity_resolution import (  # noqa: E402
    AliasFact,
    EntityFact,
    RelationshipFact,
    resolve_entities,
)
from event_collector.kb_query_expansion import ExpansionError, build_query_expansion  # noqa: E402


def _facts():
    entities = (
        EntityFact("entity:company:msft", "company", "Microsoft Corporation", "MSFT"),
        EntityFact("entity:product:azure", "product", "Microsoft Azure", None),
        EntityFact("entity:company:openai", "company", "OpenAI", None),
    )
    aliases = (
        AliasFact(
            "alias_weiruan",
            "entity:company:msft",
            "微软",
            "strong",
            "unique",
            language="zh-CN",
        ),
        AliasFact("alias_microsoft", "entity:company:msft", "Microsoft", "strong", "unique"),
        AliasFact("alias_azure", "entity:product:azure", "Azure", "strong", "unique"),
        AliasFact("alias_openai", "entity:company:openai", "OpenAI", "strong", "unique"),
    )
    relationships = (
        RelationshipFact("rel_owns", "entity:company:msft", "owns_product", "entity:product:azure"),
        RelationshipFact("rel_dep", "entity:company:msft", "depends_on", "entity:company:openai"),
    )
    return entities, aliases, relationships


def test_chinese_resolution_expands_to_deterministic_english_direct_terms():
    entities, aliases, relationships = _facts()
    decision = resolve_entities("微软最近怎么样？", entities, aliases, relationships)

    expansion = build_query_expansion(
        "微软最近怎么样？",
        decision,
        entities=entities,
        aliases=aliases,
        relationships=relationships,
        retrieval_intent="direct",
    )

    assert expansion.relationship_hops == 0
    assert [term.term for term in expansion.included_terms][:3] == [
        "MSFT",
        "Microsoft Corporation",
        "Microsoft",
    ]
    assert "Azure" in [term.term for term in expansion.included_terms]
    assert expansion.effective_query.startswith("微软最近怎么样？ MSFT Microsoft Corporation")


def test_indirect_expansion_is_bounded_to_one_verified_relationship_hop():
    entities, aliases, relationships = _facts()
    decision = resolve_entities("MSFT dependencies", entities, aliases, relationships)

    expansion = build_query_expansion(
        "MSFT dependencies",
        decision,
        entities=entities,
        aliases=aliases,
        relationships=relationships,
        retrieval_intent="indirect",
    )

    assert expansion.relationship_hops == 1
    assert "OpenAI" in [term.term for term in expansion.included_terms]
    assert len(expansion.included_terms) <= 12


def test_unresolved_target_cannot_build_target_specific_expansion():
    entities, aliases, relationships = _facts()
    decision = resolve_entities("general market outlook", entities, aliases, relationships)

    with pytest.raises(ExpansionError) as caught:
        build_query_expansion(
            "general market outlook",
            decision,
            entities=entities,
            aliases=aliases,
            relationships=relationships,
            retrieval_intent="direct",
        )

    assert caught.value.code == "UNRESOLVED_TARGET"
