"""Console and markdown presentation for watchlist results."""

from __future__ import annotations

from event_collector.watchlist_domain import WatchlistRunResult, normalize_ticker


def render_watchlist_report(
    result: WatchlistRunResult,
    debug_review: bool = False,
    debug_rerank: bool = False,
    retrieval_provenance: dict[str, object] | None = None,
) -> str:
    ranking = {item.ticker: item for item in result.ranked_items}
    ordered_records = sorted(result.items, key=lambda item: ranking[normalize_ticker(item.ticker)].rank)
    lines = [
        "# Watchlist Triage Report",
        "",
        f"- Run ID: {result.run_id}",
        f"- Generated at: {result.run_at.isoformat()}",
        f"- Watchlist size: {len(result.tickers)}",
        f"- Top N: {result.top_n}",
        f"- Retrieval top-k: {result.retrieval_top_k}",
        "",
        "## Top Summary",
        "",
        "| Rank | Ticker | Priority | Confidence | Human Review Flag |",
        "| --- | --- | --- | --- | --- |",
    ]
    if retrieval_provenance is not None:
        from event_collector.publication_provenance import render_publication_provenance_markdown

        lines[7:7] = ["", *render_publication_provenance_markdown(retrieval_provenance), ""]
    for ranked in result.ranked_items[: result.top_n]:
        lines.append(
            f"| {ranked.rank} | {ranked.ticker} | {ranked.priority.value} | "
            f"{ranked.confidence.value} | {'yes' if ranked.should_flag_human_review else 'no'} |"
        )

    for item in ordered_records:
        rank = ranking[normalize_ticker(item.ticker)].rank
        lines.extend(
            [
                "",
                f"## {rank}. {normalize_ticker(item.ticker)}",
                "",
                f"- Priority: {item.card.priority.value}",
                f"- Confidence: {item.card.confidence.value}",
                f"- Flag human review: {'yes' if item.reviewer_finding.should_flag_human_review else 'no'}",
                "",
                "### Why Now",
                item.card.why_now,
                "",
                "### Key Evidence",
            ]
        )
        if item.card.key_evidence:
            for evidence_item in item.card.key_evidence:
                lines.append(f"- {evidence_item}")
        else:
            lines.append("- None")

        lines.extend(["", "### Counter Evidence"])
        if item.card.counter_evidence:
            for evidence_item in item.card.counter_evidence:
                lines.append(f"- {evidence_item}")
        else:
            lines.append("- None")

        lines.extend(["", "### Missing Questions"])
        if item.card.missing_questions:
            for question in item.card.missing_questions:
                lines.append(f"- {question}")
        else:
            lines.append("- None")

        lines.extend(
            [
                "",
                "### Next Action",
                item.card.next_action,
                "",
                "### Reviewer",
                item.reviewer_finding.summary,
            ]
        )
        if debug_review:
            lines.extend(
                [
                    "",
                    f"- evidence_too_generic: {str(item.reviewer_finding.evidence_too_generic).lower()}",
                    (
                        "- missing_target_specific_signal: "
                        f"{str(item.reviewer_finding.missing_target_specific_signal).lower()}"
                    ),
                    f"- reasoning_jump: {str(item.reviewer_finding.reasoning_jump).lower()}",
                    (
                        "- missing_counter_evidence: "
                        f"{str(item.reviewer_finding.missing_counter_evidence).lower()}"
                    ),
                    f"- next_action_too_vague: {str(item.reviewer_finding.next_action_too_vague).lower()}",
                ]
            )

        lines.extend(["", "### Evidence"])
        if item.evidence:
            for evidence_item in item.evidence:
                lines.append(f"- [{evidence_item.source_id}] {evidence_item.title} ({evidence_item.url})")
                lines.append(f"  - Snippet: {evidence_item.snippet}")
        else:
            lines.append("- No retrieved evidence")

        if item.structured_signals:
            lines.extend(["", "### Structured Signals"])
            for signal in item.structured_signals:
                lines.append(
                    (
                        f"- [{signal.source_id}] {signal.event_type} / {signal.direction} / "
                        f"{signal.importance} / {signal.time_horizon}: {signal.reasoning}"
                    )
                )

        if debug_rerank and item.rerank_metadata is not None:
            lines.extend(["", "### Rerank Debug"])
            for index, candidate in enumerate(item.rerank_metadata.ranked_candidates, start=1):
                lines.append(f"{index}. Candidate {candidate.candidate_id}: {candidate.reason}")

    return "\n".join(lines).strip() + "\n"


def render_watchlist_summary(
    result: WatchlistRunResult,
    debug_review: bool = False,
    debug_rerank: bool = False,
) -> str:
    ranking = {item.ticker: item for item in result.ranked_items}
    ordered_records = sorted(result.items, key=lambda item: ranking[normalize_ticker(item.ticker)].rank)
    lines = [
        "Watchlist Triage:",
        f"Run ID: {result.run_id}",
        "",
        "Top Watchlist:",
    ]
    for item in ordered_records[: result.top_n]:
        ranked = ranking[normalize_ticker(item.ticker)]
        lines.append(
            f"{ranked.rank}. {ranked.ticker} - {ranked.priority.value} / {ranked.confidence.value}"
            f"{' [review]' if ranked.should_flag_human_review else ''}"
        )
        lines.append(f"   Why now: {item.card.why_now}")

        if item.card.key_evidence:
            lines.append(f"   Key evidence: {item.card.key_evidence[0]}")
            for evidence_item in item.card.key_evidence[1:3]:
                lines.append(f"     - {evidence_item}")
        else:
            lines.append("   Key evidence: None")

        if item.card.counter_evidence:
            lines.append(f"   Counter evidence: {item.card.counter_evidence[0]}")
            for evidence_item in item.card.counter_evidence[1:3]:
                lines.append(f"     - {evidence_item}")

        if item.card.missing_questions:
            lines.append(f"   Missing question: {item.card.missing_questions[0]}")

        lines.append(f"   Next action: {item.card.next_action}")
        lines.append(f"   Reviewer: {item.reviewer_finding.summary}")

        if debug_review:
            lines.append(
                "   Review flags: "
                f"generic={str(item.reviewer_finding.evidence_too_generic).lower()}, "
                f"target_specific_missing={str(item.reviewer_finding.missing_target_specific_signal).lower()}, "
                f"reasoning_jump={str(item.reviewer_finding.reasoning_jump).lower()}, "
                f"counter_evidence_missing={str(item.reviewer_finding.missing_counter_evidence).lower()}, "
                f"next_action_vague={str(item.reviewer_finding.next_action_too_vague).lower()}"
            )

        if item.structured_signals:
            signal = item.structured_signals[0]
            lines.append(
                "   Structured signal: "
                f"{signal.event_type}/{signal.direction}/{signal.importance}/{signal.time_horizon} - "
                f"{signal.reasoning}"
            )

        if debug_rerank and item.rerank_metadata is not None and item.rerank_metadata.ranked_candidates:
            rerank_reason = item.rerank_metadata.ranked_candidates[0]
            lines.append(f"   Rerank: candidate {rerank_reason.candidate_id} kept because {rerank_reason.reason}")

        lines.append("")
    return "\n".join(lines).rstrip()
