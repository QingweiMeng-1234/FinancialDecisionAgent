from __future__ import annotations

from event_collector.rag_answering import answer_query, render_citation_list
from event_collector.retrieval_intent import DEFAULT_RETRIEVAL_INTENT


def render_rag_answer(result, debug_rerank: bool = False) -> str:
    lines = [
        "Answer:",
        result.answer,
        "",
        f"Confidence: {result.confidence.value}",
        f"Insufficient evidence: {'yes' if result.insufficient_evidence else 'no'}",
    ]

    if result.supporting_points:
        lines.extend(["", "Supporting Points:"])
        for point in result.supporting_points:
            lines.append(f"- {point.text} {render_citation_list(point.citations)}".rstrip())

    if result.counter_points:
        lines.extend(["", "Counter Points:"])
        for point in result.counter_points:
            lines.append(f"- {point.text} {render_citation_list(point.citations)}".rstrip())

    if result.sources:
        lines.extend(["", "Sources:"])
        for source in result.sources:
            lines.append(f"[{source.id}] {source.title}")
            lines.append(f"URL: {source.url}")
            lines.append(f"Snippet: {source.snippet}")
            lines.append("")

        while lines and lines[-1] == "":
            lines.pop()

    if debug_rerank and getattr(result, "rerank_metadata", None):
        lines.extend(["", "Rerank Debug:"])
        for index, item in enumerate(result.rerank_metadata.ranked_candidates, start=1):
            lines.append(f"{index}. Candidate {item.candidate_id}: {item.reason}")

    return "\n".join(lines)


def run_question(
    question,
    vector_store,
    top_k: int = 3,
    retrieval_top_k: int = 5,
    answer_fn=answer_query,
    debug_rerank: bool = False,
    retrieval_intent=DEFAULT_RETRIEVAL_INTENT,
) -> str:
    result = answer_fn(
        question,
        vector_store,
        top_k=top_k,
        retrieval_top_k=retrieval_top_k,
        retrieval_intent=retrieval_intent,
    )
    return render_rag_answer(result, debug_rerank=debug_rerank)
