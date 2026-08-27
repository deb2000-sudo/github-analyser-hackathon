from __future__ import annotations

from typing import Any

RELEVANCE_MIN = 3.0


def normalize_solution_fit(section: dict[str, Any]) -> dict[str, Any]:
    """Enforce relevance gate — unrelated repos get zero alignment."""
    out = dict(section)

    try:
        relevance = float(out.get("relevance_score") if out.get("relevance_score") is not None else 10.0)
    except (TypeError, ValueError):
        relevance = 0.0
    relevance = max(0.0, min(10.0, relevance))

    context_relevant = out.get("context_relevant")
    if context_relevant is None:
        context_relevant = relevance >= RELEVANCE_MIN
    else:
        context_relevant = bool(context_relevant)

    if not context_relevant or relevance < RELEVANCE_MIN:
        out["context_relevant"] = False
        out["relevance_score"] = relevance
        out["alignment_score"] = 0.0
        out["implements_claimed_solution"] = False
        if not out.get("reasoning"):
            out["reasoning"] = (
                "Repository does not match the project context — different product or domain."
            )
        return out

    out["context_relevant"] = True
    out["relevance_score"] = relevance

    try:
        alignment = float(out.get("alignment_score") if out.get("alignment_score") is not None else 0.0)
    except (TypeError, ValueError):
        alignment = 0.0
    out["alignment_score"] = max(0.0, min(10.0, alignment))
    out["implements_claimed_solution"] = bool(out.get("implements_claimed_solution")) and alignment >= 5.0

    # Support legacy LLM field name
    if out.get("context_requirements_met") is None and out.get("requirements_met") is not None:
        out["context_requirements_met"] = out["requirements_met"]

    return out
