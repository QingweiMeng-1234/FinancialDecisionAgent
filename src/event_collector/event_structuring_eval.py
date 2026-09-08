"""Read-only, uncached event extraction evaluation and reviewed SFT export."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
from time import perf_counter

from event_collector.event_structuring import ArticleForStructuring, StructuredEventResponse


LABEL_FIELDS = ("event_type", "direction", "importance", "time_horizon", "affected_asset")


def load_cases(path):
    cases = [json.loads(line) for line in Path(path).read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    ids, groups, contents = set(), {}, {}
    for case in cases:
        case_id = case["case_id"]
        if not isinstance(case_id, str) or not case_id.strip() or case_id in ids:
            raise ValueError("case_id must be nonempty and unique")
        ids.add(case_id)
        if case.get("split") not in {"train", "validation", "test"}:
            raise ValueError("split must be train, validation or test")
        if not isinstance(case.get("group_id"), str) or not case["group_id"].strip():
            raise ValueError("group_id is required for story split isolation")
        if type(case.get("reviewed")) is not bool:
            raise ValueError("reviewed must be a boolean")
        article = ArticleForStructuring(**case["article"])
        if not isinstance(article.content, str) or not article.content.strip():
            raise ValueError("article body is required")
        content_hash = hashlib.sha256(" ".join(article.content.split()).encode()).hexdigest()
        for lookup, key in ((groups, case["group_id"]), (contents, content_hash)):
            if key in lookup and lookup[key] != case["split"]:
                raise ValueError("the same story or content occurs across dataset splits")
            lookup[key] = case["split"]
        if case["reviewed"] and "expected_events" not in case:
            raise ValueError("reviewed cases require expected_events, including [] for negatives")
        if "expected_events" in case:
            StructuredEventResponse.model_validate({"events": case["expected_events"]})
    if not cases:
        raise ValueError("dataset is empty")
    return cases


def _labels(events):
    return Counter(tuple(str(event[k]).strip() for k in LABEL_FIELDS) for event in events)


def evaluate_cases(cases, client):
    """Match complete categorical tuples one-to-one; rationales are not auto-graded."""
    rows = []
    tp = predicted = expected = exact = reviewed = successful = quote_ok = quote_total = 0
    for case in cases:
        article = ArticleForStructuring(**case["article"])
        started = perf_counter()
        events = []
        row = {"case_id": case["case_id"], "status": "failed"}
        try:
            response = StructuredEventResponse.model_validate(client.extract_events(article))
            events = response.model_dump(mode="json")["events"]
            successful += 1
            row["status"] = "success"
        except Exception as error:
            # No request bodies, credentials or raw provider errors in reports.
            row["error_type"] = type(error).__name__
        row.update(elapsed_seconds=perf_counter() - started, events=events,
                   trace=getattr(client, "last_trace", {}))
        for event in events:
            quote_total += 1
            quote_ok += bool(event["evidence_excerpt"].strip()) and event["evidence_excerpt"] in article.content
        if case.get("reviewed") is True and "expected_events" in case:
            reviewed += 1
            gold = _labels(case["expected_events"])
            actual = _labels(events)
            tp += sum((gold & actual).values())
            expected += sum(gold.values())
            predicted += sum(actual.values())
            matched = row["status"] == "success" and gold == actual
            exact += matched
            row["exact_labels_match"] = matched
        rows.append(row)
    precision = tp / predicted if predicted else 0.0
    recall = tp / expected if expected else 0.0
    latency = sorted(row["elapsed_seconds"] for row in rows)
    metrics = {
        "cases": len(rows), "reviewed_cases": reviewed,
        "success_rate": successful / len(rows) if rows else None,
        "event_precision": precision if reviewed and predicted else None,
        "event_recall": recall if reviewed and expected else None,
        "event_f1": 2 * tp / (predicted + expected) if reviewed and predicted + expected else None,
        "exact_article_accuracy": exact / reviewed if reviewed else None,
        "verbatim_excerpt_rate": quote_ok / quote_total if quote_total else None,
        "mean_latency_seconds": sum(latency) / len(rows) if rows else None,
        "p95_latency_seconds": latency[max(0, math.ceil(len(rows) * .95) - 1)] if rows else None,
        "fallback_count": sum(bool(row["trace"].get("fallback_used")) for row in rows),
    }
    return {"schema_version": "event-structuring-eval-v1", "metrics": metrics, "cases": rows,
            "quality_scope": "Exact categorical tuples only; verbatim quotes do not prove semantic support. Unreviewed cases have no quality score."}


def export_sft(cases):
    from event_collector.local_event_structuring import LOCAL_EVENT_SYSTEM_PROMPT
    from event_collector.event_structuring import _format_article

    records = []
    for case in cases:
        if case.get("split") != "train" or case.get("reviewed") is not True:
            continue
        article = ArticleForStructuring(**case["article"])
        events = case["expected_events"]
        for event in events:
            if not event["evidence_excerpt"].strip() or event["evidence_excerpt"] not in article.content:
                raise ValueError(f"{case['case_id']}: SFT evidence must be verbatim article text")
        records.append({"messages": [
            {"role": "system", "content": LOCAL_EVENT_SYSTEM_PROMPT},
            {"role": "user", "content": _format_article(article)},
            {"role": "assistant", "content": json.dumps({"events": events}, ensure_ascii=False)},
        ]})
    return records


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--export-sft", action="store_true")
    parser.add_argument("--split", choices=["train", "validation", "test"], default="test")
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error("output exists; choose a new report path")
    cases = load_cases(args.dataset)
    if args.export_sft:
        records = export_sft(cases)
        if not records:
            parser.error("no reviewed train cases; do not train on unreviewed predictions")
        output = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in records)
    else:
        from event_collector.local_event_structuring import build_event_structuring_client

        selected = [case for case in cases if case["split"] == args.split]
        if not selected:
            parser.error("no cases in selected split")
        result = evaluate_cases(selected, build_event_structuring_client())
        result["dataset_sha256"] = hashlib.sha256(args.dataset.read_bytes()).hexdigest()
        result["split"] = args.split
        output = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        print(json.dumps(result["metrics"], ensure_ascii=False, indent=2))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        stream.write(output)


if __name__ == "__main__":
    main()
