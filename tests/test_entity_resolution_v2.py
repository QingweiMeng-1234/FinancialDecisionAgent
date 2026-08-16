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
    EntityResolutionError,
    RelationshipFact,
    resolve_entities,
)


def _facts():
    entities = (
        EntityFact("entity:company:msft", "company", "Microsoft Corporation", "MSFT"),
        EntityFact("entity:company:aapl", "company", "Apple Inc.", "AAPL"),
        EntityFact("entity:company:target", "company", "Target Corporation", "TGT"),
        EntityFact("entity:company:target_h", "company", "Target Hospitality", "TH"),
        EntityFact("entity:product:azure", "product", "Microsoft Azure", None),
    )
    aliases = (
        AliasFact("alias_msft", "entity:company:msft", "Microsoft", "strong", "unique"),
        AliasFact("alias_weiruan", "entity:company:msft", "微软", "strong", "unique"),
        AliasFact("alias_aapl", "entity:company:aapl", "Apple", "strong", "unique"),
        AliasFact("alias_target_1", "entity:company:target", "Target", "strong", "ambiguous"),
        AliasFact("alias_target_2", "entity:company:target_h", "Target", "strong", "ambiguous"),
        AliasFact("alias_azure", "entity:product:azure", "Azure", "strong", "unique"),
    )
    relationships = (
        RelationshipFact(
            "rel_msft_azure",
            "entity:company:msft",
            "owns_product",
            "entity:product:azure",
        ),
    )
    return entities, aliases, relationships


@pytest.mark.parametrize(
    ("query", "reason"),
    [
        ("What changed for MSFT cloud demand?", "EXACT_TICKER"),
        ("微软最近的 Azure 需求有什么变化？", "STRONG_ALIAS"),
        ("Microsoft Corporation earnings outlook", "CANONICAL_NAME"),
    ],
)
def test_resolves_company_from_full_natural_language_query(query, reason):
    entities, aliases, relationships = _facts()

    decision = resolve_entities(query, entities, aliases, relationships)

    assert decision.status == "resolved"
    assert decision.selected_entity.entity_id == "entity:company:msft"
    assert reason in decision.reason_codes


def test_ambiguous_ordinary_alias_never_auto_resolves():
    entities, aliases, relationships = _facts()

    decision = resolve_entities("What changed at Target?", entities, aliases, relationships)

    assert decision.status == "ambiguous"
    assert decision.selected_entity is None
    assert {entity.entity_id for entity in decision.candidate_entities} == {
        "entity:company:target",
        "entity:company:target_h",
    }
    assert decision.reason_codes == ("MULTIPLE_STRONG_MATCHES",)


def test_product_only_mention_produces_owner_candidate_but_no_selected_company():
    entities, aliases, relationships = _facts()

    decision = resolve_entities("How is Azure demand changing?", entities, aliases, relationships)

    assert decision.status == "unresolved"
    assert decision.selected_entity is None
    assert [entity.entity_id for entity in decision.candidate_entities] == [
        "entity:company:msft"
    ]
    assert decision.reason_codes == ("PRODUCT_OWNER_CANDIDATE_ONLY",)


def test_explicit_target_conflicting_with_query_fails_closed():
    entities, aliases, relationships = _facts()

    with pytest.raises(EntityResolutionError) as caught:
        resolve_entities(
            "What changed for Apple?",
            entities,
            aliases,
            relationships,
            target_entity_id="entity:company:msft",
        )

    assert caught.value.code == "TARGET_ENTITY_MISMATCH"
