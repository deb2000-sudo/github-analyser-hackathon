"""Analyze one already-fetched snapshot. Parses the repository once."""

from __future__ import annotations

import time
from typing import Any

from app.analysis.context import attach_analysis, build_analysis_context, empty_timings
from app.analysis.evidence_aggregator import EvidenceAggregator
from app.analysis.report import build_analysis_result
from app.config import get_settings
from app.github.client import RepoSnapshot
from app.github.validation import RepoAccessInfo, access_payload
from app.llm.selective import decide_llm_use
from app.metrics.ai_usage import scan_manifests
from app.metrics.base import MetricContext
from app.metrics.registry import get_metric
from app.scoring.aggregator import aggregate_scores

PIPELINE_ORDER = [
    "repo_health",
    "fullstack",
    "frontend_backend",
    "ai_usage",
    "agent_analysis",
    "solution_fit",
]


def _ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))


async def analyze_snapshot(
    snapshot: RepoSnapshot,
    *,
    access: RepoAccessInfo,
    github_url: str,
    requested: list[str],
    options: dict[str, Any],
    submission_context: dict[str, Any],
    request_scoring: dict[str, Any] | None,
    llm: Any,
    fetch_ms: int = 0,
    started_at: float | None = None,
) -> dict[str, Any]:
    """Inventory → parse → graphs → AnalysisContext → metrics. One parse per call."""
    clock = time.perf_counter() if started_at is None else started_at
    settings = get_settings()
    ai_deps, agent_deps = scan_manifests(snapshot.package_manifests)

    analysis = build_analysis_context(
        snapshot,
        submission_context=submission_context,
    )
    analysis.timings["fetch_ms"] = int(fetch_ms)

    to_run = list(dict.fromkeys([*requested, "repo_health"]))
    ordered = [m for m in PIPELINE_ORDER if m in to_run]
    llm_candidates = [m for m in ("agent_analysis", "solution_fit") if m in ordered]
    static_first = [m for m in ordered if m not in llm_candidates]
    llm_after = [m for m in ordered if m in llm_candidates]

    results: dict[str, Any] = {}
    ctx = MetricContext(
        snapshot=snapshot,
        prior_results=results,
        extras={
            "llm_client": llm,
            "submission_context": submission_context,
            "ai_dependencies_found": ai_deps,
            "agent_frameworks_found": agent_deps,
            "llm_judgment": {},
            "skip_file_fetch": True,
        },
    )
    attach_analysis(ctx, analysis)

    metrics_started = time.perf_counter()
    for name in static_first:
        metric = get_metric(name)
        if not metric:
            continue
        if name != "repo_health" and name not in requested:
            continue
        ctx.options = options.get(name) or {}
        result = await metric.run(ctx)
        results[name] = {"status": result.status, **(result.data or {})}
        if result.reason:
            results[name]["skip_reason"] = result.reason
        ctx.prior_results = results
    analysis.timings["metrics_ms"] = _ms(metrics_started)

    llm_plan = decide_llm_use(
        requested=requested,
        llm_enabled=bool(getattr(llm, "enabled", False)),
        static_metrics=results,
        submission_context=submission_context,
        code_facts=analysis.code_facts,
        agent_deps=agent_deps,
        confidence_threshold=settings.llm_confidence_threshold,
    )
    ctx.extras["llm_plan"] = llm_plan
    ctx.extras["llm_judgment"] = {}

    llm_started = time.perf_counter()
    for name in llm_after:
        metric = get_metric(name)
        if not metric:
            continue
        ctx.options = options.get(name) or {}
        ctx.prior_results = results
        result = await metric.run(ctx)
        results[name] = {"status": result.status, **(result.data or {})}
        if result.reason:
            results[name]["skip_reason"] = result.reason
        ctx.prior_results = results
    analysis.timings["llm_ms"] = _ms(llm_started)
    analysis.timings["total_ms"] = _ms(clock)

    scoring = aggregate_scores(
        results,
        request_scoring=request_scoring,
        access=access,
        snapshot=snapshot,
    )
    verdict = EvidenceAggregator().aggregate(metrics=results, code_facts=analysis.code_facts)
    analysis_payload = analysis.code_facts.to_public_dict()
    analysis_payload["verdict"] = verdict
    repository = {
        "owner": snapshot.ref.owner,
        "name": snapshot.ref.name,
        "full_name": snapshot.ref.full_name,
        "default_branch": snapshot.ref.default_branch,
        "commit_sha": snapshot.ref.commit_sha,
        "github_url": github_url,
    }
    return build_analysis_result(
        access=access_payload(access),
        repository=repository,
        metrics=results,
        scoring=scoring,
        verdict=verdict,
        code_facts=analysis.code_facts,
        context=submission_context or None,
        llm_plan=llm_plan,
        analysis=analysis_payload,
        timings=analysis.timing_dict(),
    )


def gated_timings(*, total_ms: int = 0) -> dict[str, int]:
    timings = empty_timings()
    timings["total_ms"] = int(total_ms)
    return timings
