import json

import pytest

from event_collector.event_structuring_eval import evaluate_cases, export_sft, load_cases


def case(case_id="a", **extra):
    return dict(case_id=case_id, group_id="story-a", split="test", reviewed=True,
                article=dict(article_id=1, title="Notice", description="", content="Acme closed a factory.", url=""),
                expected_events=[dict(event_type="Company", direction="Negative", importance="Medium",
                    time_horizon="Long-term", affected_asset="Acme", reasoning="Factory closure.",
                    evidence_excerpt="Acme closed a factory.")], **extra)


class Client:
    model = "test"
    last_trace = {}
    def __init__(self, response):
        self.response = response
    def extract_events(self, article):
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def test_eval_counts_failures_and_does_not_treat_format_as_accuracy():
    good = case()
    result = evaluate_cases([good], Client({"events": good["expected_events"]}))
    assert result["metrics"]["event_f1"] == 1
    assert result["metrics"]["exact_article_accuracy"] == 1
    wrong = {**good["expected_events"][0], "affected_asset": "OtherCo"}
    result = evaluate_cases([good], Client({"events": [wrong]}))
    assert result["metrics"]["success_rate"] == 1
    assert result["metrics"]["event_f1"] == 0
    failed = evaluate_cases([good], Client(RuntimeError("payload or secret must not leak")))
    assert failed["metrics"]["event_recall"] == 0
    assert "secret" not in json.dumps(failed)


def test_eval_matches_duplicate_events_one_to_one():
    good = case()
    result = evaluate_cases([good], Client({"events": good["expected_events"] * 2}))
    assert result["metrics"]["event_precision"] == 0.5
    assert result["metrics"]["exact_article_accuracy"] == 0


def test_unreviewed_and_unlabeled_cases_have_no_quality_score():
    unlabeled = case()
    unlabeled.pop("expected_events")
    unlabeled["reviewed"] = False
    result = evaluate_cases([unlabeled], Client({"events": []}))
    assert result["metrics"]["reviewed_cases"] == 0
    assert result["metrics"]["event_f1"] is None


def test_dataset_rejects_story_leakage_and_missing_reviewed_labels(tmp_path):
    path = tmp_path / "cases.jsonl"
    other = {**case("b"), "split": "train"}
    path.write_text('\n'.join(json.dumps(c) for c in [case(), other]), encoding="utf-8")
    with pytest.raises(ValueError, match="split"):
        load_cases(path)
    invalid = case()
    invalid.pop("expected_events")
    path.write_text(json.dumps(invalid), encoding="utf-8")
    with pytest.raises(ValueError, match="expected_events"):
        load_cases(path)


def test_sft_export_only_uses_reviewed_train_and_verbatim_evidence():
    train = {**case(), "split": "train"}
    assert len(export_sft([train, {**case("b"), "reviewed": False}])) == 1
    assert export_sft([case()]) == []
    train["expected_events"][0]["evidence_excerpt"] = "invented quote"
    with pytest.raises(ValueError, match="evidence"):
        export_sft([train])
