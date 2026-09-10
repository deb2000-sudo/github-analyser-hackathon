"""Phase 12: 0–100 AI integration score from deterministic evidence.

Used by the existing 'Uses an LLM' rubric. Point values are configurable.
Does not call Gemini.
"""

from __future__ import annotations

from typing import Any

from app.analysis.ai_fake import (
    RULE_DISCARDED,
    RULE_HARDCODED,
    RULE_MOCK,
    RULE_POOL,
    RULE_UNUSED_DEP,
)
from app.analysis.evidence_aggregator import EvidenceAggregator

DEFAULT_POINTS: dict[str, int] = {
    "dependency": 5,
    "import": 5,
    "client": 10,
    "invocation": 20,
    "input_flow": 25,
    "output_used": 20,
    "complete_flow": 15,
}

DEFAULT_PENALTIES: dict[str, int] = {
    "unused_dependency": -20,
    "hardcoded_response": -40,
    "static_pool": -35,
    "output_discarded": -25,
    "mock_only": -40,
}

EVIDENCE_KEYS = frozenset(
    {
        "dependency_detected",
        "import_detected",
        "client_detected",
        "model_invocation_detected",
        "user_input_reaches_model",
        "model_output_used",
        "ai_findings",
        "ai_verification",
    }
)


def has_deterministic_ai_signals(ai: dict[str, Any]) -> bool:
    return any(key in ai for key in EVIDENCE_KEYS)


def resolve_ai_points(overrides: dict[str, Any] | None = None) -> tuple[dict[str, int], dict[str, int]]:
    points = dict(DEFAULT_POINTS)
    penalties = dict(DEFAULT_PENALTIES)
    block = overrides or {}
    for key, value in (block.get("points") or {}).items():
        if key in points:
            points[key] = int(value)
    for key, value in (block.get("penalties") or {}).items():
        if key in penalties:
            penalties[key] = int(value)
    return points, penalties


def score_ai_integration(
    metrics: dict[str, Any],
    *,
    point_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the 0–100 evidence score plus explanations. Not a rubric score."""
    ai = metrics.get("ai_usage") or {}
    verification = ai.get("ai_verification") or {}
    findings = list(ai.get("ai_findings") or [])
    failed = {str(item.get("rule_id")) for item in findings if item.get("status") == "failed"}
    warned = {str(item.get("rule_id")) for item in findings if item.get("status") == "warning"}

    dependency = bool(ai.get("dependency_detected") or ai.get("ai_dependencies_found"))
    imported = bool(ai.get("import_detected"))
    client = bool(ai.get("client_detected"))
    invocation = bool(
        ai.get("model_invocation_detected") or verification.get("model_invocation_detected")
    )
    input_reaches = bool(ai.get("user_input_reaches_model"))
    output_used = bool(ai.get("model_output_used"))
    discarded = RULE_DISCARDED in failed
    complete = invocation and input_reaches and output_used and not discarded
    unused_dep = RULE_UNUSED_DEP in failed or (dependency and not invocation)
    mock_only = (RULE_MOCK in warned or RULE_MOCK in failed) and not invocation

    points, penalties = resolve_ai_points(point_overrides)
    positive: list[dict[str, Any]] = []
    negative: list[dict[str, Any]] = []
    raw = 0

    def _award(key: str, enabled: bool, reason: str) -> None:
        nonlocal raw
        if not enabled:
            return
        value = int(points[key])
        raw += value
        positive.append({"id": key, "points": value, "reason": reason})

    def _penalize(key: str, enabled: bool, reason: str) -> None:
        nonlocal raw
        if not enabled:
            return
        value = int(penalties[key])
        raw += value
        negative.append({"id": key, "points": value, "reason": reason})

    _award("dependency", dependency, "AI SDK / framework package declared")
    _award("import", imported, "AI SDK import present in source")
    _award("client", client, "AI client or model object constructed")
    _award("invocation", invocation, "Actual model / inference invocation")
    _award("input_flow", input_reaches, "Dynamic application data reaches the model")
    _award("output_used", output_used, "Model output is consumed by the application")
    _award("complete_flow", complete, "End-to-end flow: user input → model → application sink")

    _penalize("unused_dependency", unused_dep, "AI dependency declared without a real invocation")
    _penalize("hardcoded_response", RULE_HARDCODED in failed, "Handler returns a hardcoded final response")
    _penalize("static_pool", RULE_POOL in failed, "Response is chosen from a static string pool")
    _penalize("output_discarded", discarded, "Model output is computed but not used")
    _penalize("mock_only", mock_only, "Implementation is mock/fake-named without a real model call")

    score = max(0, min(100, raw))
    verdict = EvidenceAggregator().aggregate(metrics=metrics)
    classification = str(verdict.get("ai_classification") or "NO_AI").lower()
    return {
        "ai_integration_score": score,
        "raw_total": raw,
        "positive_evidence": positive,
        "penalties": negative,
        "classification": classification,
        "confidence": verdict.get("confidence"),
        "ai_level": verdict.get("ai_level"),
        "explanation": _explain(score, positive, negative, classification),
    }


def _explain(
    score: int,
    positive: list[dict[str, Any]],
    penalties: list[dict[str, Any]],
    classification: str,
) -> str:
    gained = ", ".join(f"{row['id']} +{row['points']}" for row in positive) or "none"
    lost = ", ".join(f"{row['id']} {row['points']}" for row in penalties) or "none"
    return (
        f"AI integration {score}/100 ({classification.replace('_', ' ')}). "
        f"Credits: {gained}. Penalties: {lost}."
    )
