from __future__ import annotations

import asyncio
from typing import Any

from app.config import get_settings
from app.gcs_cache import GcsCache
from app.github.client import GithubClient
from app.jobs import Job, JobStatus, JobStore
from app.llm.client import LLMClient
from app.metrics.base import MetricContext
from app.metrics.registry import get_metric
from app.pipeline.prompt import build_system_prompt, build_user_prompt

PIPELINE_ORDER = [
    "repo_health",
    "fullstack",
    "ai_usage",
    "agent_analysis",
    "solution_fit",
]


async def run_pipeline(job_id: str) -> None:
    store = JobStore()
    job = await asyncio.to_thread(store.get, job_id)
    if not job:
        return

    await asyncio.to_thread(store.update, job_id, status=JobStatus.running.value)

    settings = get_settings()
    gh = GithubClient(settings=settings, cache=GcsCache(settings))
    llm = LLMClient(settings=settings)

    try:
        snapshot = await gh.fetch_snapshot(job.github_url)
        await asyncio.to_thread(store.update, job_id, commit_sha=snapshot.ref.commit_sha)

        requested: list[str] = list(job.metrics_requested or [])
        options: dict[str, Any] = dict(job.options or {})
        submission_context: dict[str, Any] = dict(job.context or {})
        to_run = list(dict.fromkeys([*requested, "repo_health"]))
        ordered = [m for m in PIPELINE_ORDER if m in to_run]

        results: dict[str, Any] = {}
        ctx = MetricContext(
            snapshot=snapshot,
            prior_results=results,
            extras={
                "github_client": gh,
                "llm_client": llm,
                "submission_context": submission_context,
            },
        )

        for name in ordered:
            metric = get_metric(name)
            if not metric:
                continue
            if name != "repo_health" and name not in requested:
                continue

            ctx.options = options.get(name) or {}
            result = await metric.run(ctx)
            results[name] = {
                "status": result.status,
                **(result.data or {}),
            }
            if result.reason:
                results[name]["skip_reason"] = result.reason
            ctx.prior_results = results

        result_payload = {
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
    system = build_system_prompt(metrics)
    user = build_user_prompt(
        metrics=metrics,
        files=files,
        hints=hints,
        submission_context=submission_context,
    )
    return await llm.judge_json(system=system, user=user)
