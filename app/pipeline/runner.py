from __future__ import annotations

import asyncio
from typing import Any

from app.config import get_settings
from app.gcs_cache import GcsCache
from app.github.client import GithubClient
from app.github.validation import access_payload
from app.jobs import Job, JobStatus, JobStore
from app.llm.client import LLMClient
from app.metrics.ai_usage import scan_manifests
from app.metrics.base import MetricContext
from app.metrics.registry import get_metric
from app.metrics.solution_fit import _prior_summaries
from app.pipeline.prefetch import (
    collect_llm_files,
    collect_prefetch_paths,
    resolve_llm_metrics,
)
from app.pipeline.prompt import build_system_prompt, build_user_prompt
from app.scoring.aggregator import aggregate_scores, build_gated_result

PIPELINE_ORDER = [
    "repo_health",
    "fullstack",
    "ai_usage",
    "agent_analysis",
    "solution_fit",
]


def _request_scoring(context: dict[str, Any], options: dict[str, Any]) -> dict[str, Any] | None:
    if options.get("scoring"):
        return options["scoring"]
    if context.get("scoring"):
        return context["scoring"]
    return None


async def run_pipeline(job_id: str) -> None:
    store = JobStore()
    job = await asyncio.to_thread(store.get, job_id)
    if not job:
        return

    await asyncio.to_thread(store.update, job_id, status=JobStatus.running.value)

    settings = get_settings()
    gh = GithubClient(settings=settings, cache=GcsCache(settings))
    llm = LLMClient(settings=settings)

    requested: list[str] = list(job.metrics_requested or [])
    options: dict[str, Any] = dict(job.options or {})
    submission_context: dict[str, Any] = dict(job.context or {})
    request_scoring = _request_scoring(submission_context, options)

    try:
        access = await gh.check_repo_access(job.github_url)
        if not access.is_public:
            result_payload = build_gated_result(
                access,
                github_url=job.github_url,
                submission_context=submission_context,
                request_scoring=request_scoring,
            )
            await asyncio.to_thread(
                store.update,
                job_id,
                result=result_payload,
                status=JobStatus.succeeded.value,
                error=None,
            )
            return

        snapshot = await gh.fetch_snapshot(job.github_url)
        await asyncio.to_thread(store.update, job_id, commit_sha=snapshot.ref.commit_sha)

        ai_deps, agent_deps = scan_manifests(snapshot.package_manifests)

        prefetch_paths = collect_prefetch_paths(
            snapshot,
            requested,
            options,
            ai_deps=ai_deps,
            agent_deps=agent_deps,
        )
        if prefetch_paths:
            await gh.fetch_files(snapshot, prefetch_paths)

        llm_metrics = resolve_llm_metrics(
            requested,
            ai_deps=ai_deps,
            agent_deps=agent_deps,
            has_evaluation_context=bool((submission_context.get("provided_context") or "").strip()),
            llm_enabled=llm.enabled,
        )

        to_run = list(dict.fromkeys([*requested, "repo_health"]))
        ordered = [m for m in PIPELINE_ORDER if m in to_run]
        static_first = [m for m in ordered if m not in llm_metrics]
        llm_after = [m for m in ordered if m in llm_metrics]

        results: dict[str, Any] = {}
        ctx = MetricContext(
            snapshot=snapshot,
            prior_results=results,
            extras={
                "github_client": gh,
                "llm_client": llm,
                "submission_context": submission_context,
                "ai_dependencies_found": ai_deps,
                "agent_frameworks_found": agent_deps,
                "llm_judgment": {},
                "skip_file_fetch": True,
            },
        )

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

        llm_judgment: dict[str, Any] = {}
        if llm_metrics:
            files = collect_llm_files(
                snapshot,
                llm_metrics,
                options,
                ai_deps=ai_deps,
                agent_deps=agent_deps,
            )
            hints: dict[str, Any] = {
                "ai_dependencies_found": ai_deps,
                "agent_frameworks_found": agent_deps,
            }
            if "solution_fit" in llm_metrics:
                hints["prior_metric_summaries"] = _prior_summaries(results)
            llm_judgment = await combined_llm_judgment(
                llm,
                llm_metrics,
                files,
                hints,
                submission_context,
            )
            ctx.extras["llm_judgment"] = llm_judgment

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

        scoring = aggregate_scores(
            results,
            request_scoring=request_scoring,
            access=access,
            snapshot=snapshot,
        )
        result_payload = {
            "access": access_payload(access),
            "scoring": scoring,
            "repo": {
                "owner": snapshot.ref.owner,
                "name": snapshot.ref.name,
                "full_name": snapshot.ref.full_name,
                "default_branch": snapshot.ref.default_branch,
                "commit_sha": snapshot.ref.commit_sha,
            },
            "context": submission_context or None,
            "metrics": results,
        }
        await asyncio.to_thread(
            store.update,
            job_id,
            result=result_payload,
            status=JobStatus.succeeded.value,
            error=None,
        )
    except Exception as exc:  # noqa: BLE001
        await asyncio.to_thread(
            store.update,
            job_id,
            status=JobStatus.failed.value,
            error=str(exc),
        )


async def combined_llm_judgment(
    llm: LLMClient,
    metrics: list[str],
    files: dict[str, str],
    hints: dict[str, Any],
    submission_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Single Gemini call for all LLM metrics (was 3 sequential calls)."""
    system = build_system_prompt(metrics)
    user = build_user_prompt(
        metrics=metrics,
        files=files,
        hints=hints,
        submission_context=submission_context,
    )
    return await llm.judge_json(system=system, user=user)
