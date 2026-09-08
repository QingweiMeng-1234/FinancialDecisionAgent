"""Opt-in semantic probes against the local server, not a held-out benchmark."""
import os

import pytest

from event_collector.event_structuring import ArticleForStructuring
from event_collector.local_event_structuring import LocalEventConfig, LocalEventStructuringClient


pytestmark = pytest.mark.skipif(os.getenv("EVENT_STRUCTURING_LIVE_TESTS") != "1",
                                reason="requires explicitly enabled loopback model")


def test_live_model_does_not_miss_explicit_guidance_cut():
    article = ArticleForStructuring(0, "Revenue guidance update", "",
        "Acme Corporation lowered its full-year revenue guidance from $10 billion to $8 billion, citing weaker customer demand. The company said the revised outlook applies to the current fiscal year.", "")
    response = LocalEventStructuringClient(LocalEventConfig.from_env()).extract_events(article)
    assert any(event.event_type.value == "Company" and event.direction.value == "Negative"
               and "Acme" in event.affected_asset for event in response.events)


def test_live_model_abstains_on_nonfinancial_birthday_story():
    article = ArticleForStructuring(1, "Birthday", "",
        "A local resident celebrated a birthday with family. The birthday cake was shaped like a rocket. Guests played board games.", "")
    assert LocalEventStructuringClient(LocalEventConfig.from_env()).extract_events(article).events == []
