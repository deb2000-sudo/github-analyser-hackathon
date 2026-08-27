from __future__ import annotations

from typing import Any

from app.github.validation import RepoAccessInfo, access_payload
from app.scoring.llm_providers import detect_llm_providers
from app.scoring.readme_quality import analyze_readme, find_readme_content
from app.scoring.rubrics import resolve_rubrics

FULLSTACK_PARTIAL_RATIO = 0.2  # frontend-only or backend-only → 20% of rubric marks


def _clamp(score: float, max_score: float) -> float:
    return max(0.0, min(max_score, score))


def _fullstack_side_present(data: dict[str, Any], side: str) -> bool:
    """Use frontend_detected.present / backend_detected.present — not truthiness of the dict."""
    obj = data.get(f"{side}_detected") or {}
    if isinstance(obj, dict) and "present" in obj:
        return bool(obj.get("present"))
    repo_type = data.get("repo_type")
    if side == "frontend":
        return repo_type == "frontend"
    return repo_type == "backend"


def _score_fullstack(metrics: dict[str, Any], max_score: float) -> tuple[float, str]:
    data = metrics.get("fullstack") or {}
    if data.get("is_fullstack"):
        fe = (data.get("frontend_detected") or {}).get("stack_guess") or "frontend"
        be = (data.get("backend_detected") or {}).get("stack_guess") or "backend"
        return max_score, (
            f"Full-stack (50% weight): both frontend ({fe}) and backend ({be}) detected — "
            f"awarded full {max_score}/{max_score} for this rubric."
        )

    has_fe = _fullstack_side_present(data, "frontend")
    has_be = _fullstack_side_present(data, "backend")
    if has_fe and has_be:
        fe = (data.get("frontend_detected") or {}).get("stack_guess") or "frontend"
        be = (data.get("backend_detected") or {}).get("stack_guess") or "backend"
        return max_score, (
            f"Full-stack (50% weight): both frontend ({fe}) and backend ({be}) detected — "
            f"awarded full {max_score}/{max_score} for this rubric."
        )

    if has_fe or has_be:
        partial = _clamp(max_score * FULLSTACK_PARTIAL_RATIO, max_score)
        if has_be and not has_fe:
            side = "backend"
            stack = (data.get("backend_detected") or {}).get("stack_guess")
        else:
            side = "frontend"
            stack = (data.get("frontend_detected") or {}).get("stack_guess")
        stack_note = f" ({stack})" if stack else ""
        return partial, (
            f"Full-stack (50% weight): only {side}{stack_note} detected — "
            f"awarded 20% of rubric = {partial:.1f}/{max_score} "
            f"(both sides required for full marks)."
        )

    return 0.0, "Full-stack (50% weight): no frontend or backend stack detected — 0 marks."


def _score_ai_usage(metrics: dict[str, Any], max_score: float) -> tuple[float, str]:
    data = metrics.get("ai_usage") or {}
    llm_info = data.get("llm_providers") or data.get("gemini") or {}
    uses_llm = bool(llm_info.get("uses_llm") or llm_info.get("uses_gemini"))
    provider_names = llm_info.get("provider_names") or []
    if not provider_names and llm_info.get("uses_gemini"):
        provider_names = ["Google Gemini"]
    integration = str(data.get("ai_integration_type") or "none").lower()
    deps = data.get("ai_dependencies_found") or []
    evidence = data.get("evidence_files") or []
    llm_reason = (data.get("reasoning") or llm_info.get("reasoning") or "").strip()
    providers_note = ", ".join(provider_names[:5]) if provider_names else "none"

    if uses_llm and integration in ("wrapper", "rag", "agentic"):
        model_hints = llm_info.get("model_hints") or []
        model_note = f" Models: {', '.join(model_hints[:3])}." if model_hints else ""
        file_note = f" Files: {', '.join(evidence[:3])}." if evidence else ""
        return max_score, (
            f"Uses an LLM (20% weight): {providers_note} detected with active "
            f"{integration} integration.{model_note}{file_note} {llm_reason} "
            f"Awarded full {max_score}/{max_score}."
        ).strip()

    if uses_llm:
        return _clamp(max_score * 0.75, max_score), (
            f"Uses an LLM (20% weight): {providers_note} detected but "
            f"integration type is '{integration}'. {llm_reason} "
            f"Partial credit — {max_score * 0.75:.1f}/{max_score}."
        ).strip()

    if integration != "none" and deps:
        return _clamp(max_score * 0.6, max_score), (
            f"Uses an LLM (20% weight): LLM dependencies ({', '.join(deps[:3])}) with "
            f"{integration} integration but no specific provider identified in scanned files — "
            f"{max_score * 0.6:.1f}/{max_score}. {llm_reason}"
        ).strip()

    if deps:
        return _clamp(max_score * 0.4, max_score), (
            f"Uses an LLM (20% weight): AI packages declared ({', '.join(deps[:3])}) "
            f"but no in-code LLM provider evidence — {max_score * 0.4:.1f}/{max_score}."
        )

    return 0.0, f"Uses an LLM (20% weight): no LLM usage detected — 0 marks. {llm_reason}"


def _score_agent_analysis(metrics: dict[str, Any], max_score: float) -> tuple[float, str]:
    data = metrics.get("agent_analysis") or {}
    ai_data = metrics.get("ai_usage") or {}
    frameworks = ai_data.get("agent_frameworks_found") or []
    fw_count = len(frameworks)

    if data.get("status") == "skipped":
        return 0.0, (
            f"Real agent orchestration (20% weight): analysis skipped — "
            f"{data.get('reasoning') or 'no agent evidence'}. 0 marks."
        )

    has_orch = bool(data.get("has_real_orchestration"))
    agents = data.get("agents") or []
    agent_count = int(data.get("agent_count") or len(agents))
    llm_reason = (data.get("reasoning") or "").strip()

    if has_orch and fw_count >= 2:
        return max_score, (
            f"Real agent orchestration (20% weight): real handoff/planning logic with "
            f"{fw_count} agent framework(s) ({', '.join(frameworks[:3])}), "
            f"{agent_count} agent role(s). Full {max_score}/{max_score}. {llm_reason}"
        ).strip()

    if has_orch and fw_count >= 1:
        return _clamp(max_score * 0.85, max_score), (
            f"Real agent orchestration (20% weight): orchestration detected with "
            f"{frameworks[0] if frameworks else 'agent patterns'} — "
            f"{max_score * 0.85:.1f}/{max_score}. {llm_reason}"
        ).strip()

    if has_orch:
        return _clamp(max_score * 0.7, max_score), (
            f"Real agent orchestration (20% weight): orchestration logic found but no "
            f"known agent framework in manifests — {max_score * 0.7:.1f}/{max_score}. {llm_reason}"
        ).strip()

    if agents and fw_count >= 1:
        return _clamp(max_score * 0.5, max_score), (
            f"Real agent orchestration (20% weight): agent code present ({agent_count} role(s), "
            f"frameworks: {', '.join(frameworks[:2])}) but orchestration unclear — "
            f"{max_score * 0.5:.1f}/{max_score}. {llm_reason}"
        ).strip()

    if fw_count >= 1:
        return _clamp(max_score * 0.25, max_score), (
            f"Real agent orchestration (20% weight): agent framework declared "
            f"({', '.join(frameworks[:2])}) without demonstrated orchestration — "
            f"{max_score * 0.25:.1f}/{max_score}."
        )

    return 0.0, (
        f"Real agent orchestration (20% weight): no agent frameworks or orchestration "
        f"evidence — 0 marks. {llm_reason}"
    ).strip()


def _score_solution_fit(metrics: dict[str, Any], max_score: float) -> tuple[float, str]:
    data = metrics.get("solution_fit") or {}
    readme = data.get("readme") or {}

    if data.get("status") == "skipped":
        return 0.0, (
            f"Context fit & README (10% weight): skipped — "
            f"{data.get('reasoning') or 'no provided_context supplied'}. 0 marks."
        )

    context_relevant = data.get("context_relevant")
    try:
        relevance = float(data.get("relevance_score") if data.get("relevance_score") is not None else 0.0)
    except (TypeError, ValueError):
        relevance = 0.0
    try:
        alignment = float(data.get("alignment_score") if data.get("alignment_score") is not None else 0.0)
    except (TypeError, ValueError):
        alignment = 0.0

    fit_reason = (data.get("reasoning") or "").strip()
    readme_reason = (readme.get("reasoning") or "").strip()

    if context_relevant is False or relevance < 3.0:
        return 0.0, (
            f"Context fit & README (10% weight): repository is NOT relevant to the project context "
            f"(relevance {relevance}/10). 0 marks — unrelated repos cannot score on solution fit. "
            f"{fit_reason}"
        ).strip()

    alignment = _clamp(alignment, 10.0)
    readme_score_f = _clamp(float(readme.get("readme_quality_score") or 0.0), 10.0)
    has_setup = bool(readme.get("readme_has_local_setup"))

    # Context alignment is mandatory; README is a modifier (max 30% of rubric if context weak)
    if alignment < 3.0:
        return 0.0, (
            f"Context fit & README (10% weight): repo is related but does not implement the project context "
            f"(alignment {alignment}/10). 0 marks. {fit_reason}"
        ).strip()

    context_component = alignment / 10.0  # 0–1
    readme_component = (readme_score_f / 10.0) if has_setup else (readme_score_f / 10.0) * 0.5
    combined = context_component * 0.75 + readme_component * 0.25
    score = _clamp(combined * max_score, max_score)

    if alignment >= 7.0 and readme_score_f >= 7.0 and has_setup:
        detail = "strong context match and clear README setup"
    elif alignment >= 7.0:
        detail = f"strong context match ({alignment}/10) but README/setup incomplete"
    elif alignment >= 5.0:
        detail = f"partial context match ({alignment}/10)"
    else:
        detail = f"weak context match ({alignment}/10)"

    return score, (
        f"Context fit & README (10% weight): {detail} — relevance {relevance}/10, "
        f"alignment {alignment}/10, README {readme_score_f}/10. "
        f"Score {score:.1f}/{max_score}. Context: {fit_reason} README: {readme_reason}"
    ).strip()


METRIC_SCORERS = {
    "fullstack": _score_fullstack,
    "ai_usage": _score_ai_usage,
    "agent_analysis": _score_agent_analysis,
    "solution_fit": _score_solution_fit,
}


def enrich_metrics_for_scoring(metrics: dict[str, Any], snapshot: Any | None = None) -> dict[str, Any]:
    """Attach derived LLM provider / readme signals used by rubric scorers."""
    enriched = {k: dict(v) if isinstance(v, dict) else v for k, v in metrics.items()}

    ai = dict(enriched.get("ai_usage") or {})
    if snapshot is not None:
        ai["llm_providers"] = detect_llm_providers(
            ai.get("ai_dependencies_found") or [],
            getattr(snapshot, "file_contents", {}) or {},
            ai.get("evidence_files"),
        )
    enriched["ai_usage"] = ai

    fit = dict(enriched.get("solution_fit") or {})
    if "readme" not in fit and snapshot is not None:
        _, readme_content = find_readme_content(
            getattr(snapshot, "tree", []) or [],
            getattr(snapshot, "file_contents", {}) or {},
        )
        fit["readme"] = analyze_readme(readme_content)
    enriched["solution_fit"] = fit
    return enriched


def score_metric_rubric(
    rubric: dict[str, Any],
    metrics: dict[str, Any],
) -> dict[str, Any]:
    max_score = float(rubric.get("max_score") or 10)
    weight = float(rubric.get("weight") or 0)
    weight_percent = float(rubric.get("weight_percent", weight / 20 * 100))
    metric_name = rubric.get("metric")
    scorer = METRIC_SCORERS.get(metric_name or "")
    if scorer:
        score, reason = scorer(metrics, max_score)
    else:
        score, reason = 0.0, f"No scorer for metric '{metric_name}'"

    weighted = (score / max_score * weight) if max_score > 0 else 0.0
    return {
        "id": rubric["id"],
        "label": rubric.get("label") or rubric["id"],
        "score": round(score, 2),
        "max_score": max_score,
        "weight": weight,
        "weight_percent": round(weight_percent, 1),
        "weighted_score": round(weighted, 2),
        "metric": metric_name,
        "reason": reason,
    }


def aggregate_scores(
    metrics: dict[str, Any],
    *,
    request_scoring: dict[str, Any] | None = None,
    access: RepoAccessInfo | None = None,
    gate_reason: str | None = None,
    snapshot: Any | None = None,
) -> dict[str, Any]:
    rubrics = resolve_rubrics(request_scoring=request_scoring)
    max_total = sum(float(r.get("weight") or 0) for r in rubrics)
    scored_metrics = enrich_metrics_for_scoring(metrics, snapshot)

    if access and not access.is_public:
        reason = gate_reason or access.reason or "repository_not_accessible"
        rubric_rows = [
            {
                "id": r["id"],
                "label": r.get("label") or r["id"],
                "score": 0.0,
                "max_score": float(r.get("max_score") or 10),
                "weight": float(r.get("weight") or 0),
                "weight_percent": float(r.get("weight_percent", 0)),
                "weighted_score": 0.0,
                "metric": r.get("metric"),
                "reason": reason,
            }
            for r in rubrics
        ]
        return {
            "total_score": 0.0,
            "max_total_score": max_total,
            "rubrics": rubric_rows,
        }

    rubric_rows = [score_metric_rubric(r, scored_metrics) for r in rubrics]
    total = sum(row["weighted_score"] for row in rubric_rows)
    return {
        "total_score": round(total, 2),
        "max_total_score": max_total,
        "rubrics": rubric_rows,
    }


def build_gated_result(
    access: RepoAccessInfo,
    *,
    github_url: str,
    submission_context: dict[str, Any] | None,
    request_scoring: dict[str, Any] | None = None,
) -> dict[str, Any]:
    scoring = aggregate_scores(
        {},
        request_scoring=request_scoring,
        access=access,
        gate_reason=access.reason,
    )
    return {
        "access": access_payload(access),
        "scoring": scoring,
        "repo": {
            "owner": access.owner,
            "name": access.name,
            "full_name": f"{access.owner}/{access.name}",
            "default_branch": access.default_branch,
            "commit_sha": None,
            "github_url": github_url,
        },
        "context": submission_context or None,
        "metrics": {},
    }
