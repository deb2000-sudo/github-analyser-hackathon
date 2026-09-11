from __future__ import annotations

import asyncio
import os
import time
from typing import Any
from uuid import uuid4

from app.config import get_settings
from app.github.client import GithubClient
from app.jobs import JobStatus, JobStore
from app.llm.client import LLMClient
from app.llm.reasoner import LLMReasoner, ReasoningRequest
from app.metrics.ai_usage import scan_manifests
from app.pipeline.analyze import PIPELINE_ORDER, analyze_snapshot, gated_timings
from app.pipeline.prefetch import collect_prefetch_paths
from app.scoring.aggregator import build_gated_result

__all__ = ["PIPELINE_ORDER", "run_pipeline", "combined_llm_judgment", "worker_execution_id"]


def worker_execution_id() -> str:
    """Stable id for one Cloud Run Job execution (retries share it)."""
    execution = (os.environ.get("CLOUD_RUN_EXECUTION") or "").strip()
    if execution:
        return execution
    override = (os.environ.get("ANALYSIS_EXECUTION_ID") or "").strip()
    if override:
        return override
    return f"inline-{uuid4().hex[:8]}"


def _request_scoring(context: dict[str, Any], options: dict[str, Any]) -> dict[str, Any] | None:
    if options.get("scoring"):
        return options["scoring"]
    if context.get("scoring"):
        return context["scoring"]
    return None


def _ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))


async def run_pipeline(job_id: str, *, execution_id: str | None = None) -> None:
    store = JobStore()
    execution_id = execution_id or worker_execution_id()
    outcome = await asyncio.to_thread(store.claim, job_id, execution_id)
    if outcome in {"already_terminal", "missing"}:
        return

    job = await asyncio.to_thread(store.get, job_id)
    if not job:
        return

    settings = get_settings()
    gh = GithubClient(settings=settings)
    llm = LLMClient(settings=settings)

    requested: list[str] = list(job.metrics_requested or [])
    options: dict[str, Any] = dict(job.options or {})
    submission_context: dict[str, Any] = dict(job.context or {})
    request_scoring = _request_scoring(submission_context, options)

    try:
        started = time.perf_counter()
        access = await gh.check_repo_access(job.github_url)
        if not access.is_public:
            result_payload = build_gated_result(
                access,
                github_url=job.github_url,
                submission_context=submission_context,
                request_scoring=request_scoring,
                timings=gated_timings(total_ms=_ms(started)),
            )
            await asyncio.to_thread(
                store.complete,
                job_id,
                execution_id,
                result=result_payload,
                status=JobStatus.succeeded.value,
                error=None,
            )
            return

        fetch_started = time.perf_counter()
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
            await gh.fetch_files(snapshot, prefetch_paths, max_files=100)
        fetch_ms = _ms(fetch_started)

        result_payload = await analyze_snapshot(
            snapshot,
            access=access,
            github_url=job.github_url,
            requested=requested,
            options=options,
            submission_context=submission_context,
            request_scoring=request_scoring,
            llm=llm,
            fetch_ms=fetch_ms,
            started_at=started,
        )
        await asyncio.to_thread(
            store.complete,
            job_id,
            execution_id,
            result=result_payload,
            status=JobStatus.succeeded.value,
            error=None,
        )
    except Exception as exc:  # noqa: BLE001
        await asyncio.to_thread(
            store.complete,
            job_id,
            execution_id,
            status=JobStatus.failed.value,
            error=str(exc),
        )


async def combined_llm_judgment(
    reasoner: LLMReasoner,
    request: ReasoningRequest,
) -> dict[str, Any]:
    """Single selective Gemini call over a curated evidence pack."""
    return await reasoner.reason(request)
