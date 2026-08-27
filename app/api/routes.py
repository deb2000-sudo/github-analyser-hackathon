from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException

from app import __version__
from app.config import get_settings
from app.jobs import Job, JobStatus, JobStore, new_job_id
from app.metrics.registry import catalogue, resolve_requested
from app.pipeline.runner import run_pipeline
from app.schemas import (
    AnalyzeRequest,
    BatchAnalyzeRequest,
    BatchAnalyzeResponse,
    HealthResponse,
    JobCreatedResponse,
    JobResponse,
    MetricCatalogueEntry,
    MetricsCatalogueResponse,
    RubricCatalogueEntry,
    RubricsCatalogueResponse,
)
from app.scoring.rubrics import rubric_catalogue

router = APIRouter()


def _options_to_dict(req: AnalyzeRequest) -> dict[str, Any]:
    if not req.options:
        return {}
    return req.options.model_dump(exclude_none=True)


async def _enqueue(req: AnalyzeRequest, background: BackgroundTasks) -> Job:
    try:
        metrics = resolve_requested(req.metrics)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    context = req.context.model_dump(exclude_none=True)
    if not (context.get("provided_context") or "").strip():
        raise HTTPException(
            status_code=400, detail="context.provided_context must be a non-empty string"
        )

    job = Job(
        id=new_job_id(),
        status=JobStatus.queued.value,
        github_url=req.github_url,
        metrics_requested=metrics,
        options=_options_to_dict(req),
        context=context,
    )
    store = JobStore()
    await asyncio.to_thread(store.create, job)

    background.add_task(run_pipeline, job.id)
    return job


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(status="ok", llm_enabled=settings.llm_enabled, version=__version__)


@router.get("/metrics", response_model=MetricsCatalogueResponse)
async def list_metrics() -> MetricsCatalogueResponse:
    return MetricsCatalogueResponse(metrics=[MetricCatalogueEntry(**m) for m in catalogue()])


@router.get("/rubrics", response_model=RubricsCatalogueResponse)
async def list_rubrics() -> RubricsCatalogueResponse:
    rubrics = rubric_catalogue()
    max_total = sum(float(r.get("weight") or 0) for r in rubrics)
    return RubricsCatalogueResponse(
        rubrics=[RubricCatalogueEntry(**r) for r in rubrics],
        max_total_score=max_total,
    )


@router.post("/analyze", response_model=JobCreatedResponse)
async def analyze(body: AnalyzeRequest, background: BackgroundTasks) -> JobCreatedResponse:
    job = await _enqueue(body, background)
    return JobCreatedResponse(
        job_id=job.id,
        status="queued",
        metrics_requested=list(job.metrics_requested),
    )


@router.post("/analyze/batch", response_model=BatchAnalyzeResponse)
async def analyze_batch(
    body: BatchAnalyzeRequest, background: BackgroundTasks
) -> BatchAnalyzeResponse:
    if not body.items:
        raise HTTPException(status_code=400, detail="items must be a non-empty array")
    if len(body.items) > 50:
        raise HTTPException(status_code=400, detail="batch limit is 50 items")

    jobs: list[JobCreatedResponse] = []
    for item in body.items:
        job = await _enqueue(item, background)
        jobs.append(
            JobCreatedResponse(
                job_id=job.id,
                status="queued",
                metrics_requested=list(job.metrics_requested),
            )
        )
    return BatchAnalyzeResponse(jobs=jobs)


@router.get("/analyze/{job_id}", response_model=JobResponse)
async def get_job(job_id: str, wait_seconds: int = 0) -> JobResponse:
    """Poll job status. Pass wait_seconds (max 120) to block until done."""
    store = JobStore()
    wait_seconds = max(0, min(wait_seconds, 120))
    deadline = time.monotonic() + wait_seconds

    while True:
        job = await asyncio.to_thread(store.get, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="job not found")
        if job.status in ("succeeded", "failed") or time.monotonic() >= deadline:
            break
        await asyncio.sleep(0.5)

    return JobResponse(
        job_id=job.id,
        status=job.status,  # type: ignore[arg-type]
        github_url=job.github_url,
        metrics_requested=list(job.metrics_requested or []),
        context=job.context,
        commit_sha=job.commit_sha,
        result=job.result,
        error=job.error,
        created_at=job.created_at.isoformat() if job.created_at else None,
        updated_at=job.updated_at.isoformat() if job.updated_at else None,
    )


@router.post("/analyze/sync", response_model=JobResponse)
async def analyze_sync(body: AnalyzeRequest, wait_seconds: int = 120) -> JobResponse:
    """Submit analysis and wait for the result (max wait_seconds=120)."""
    wait_seconds = max(5, min(wait_seconds, 120))
    try:
        metrics = resolve_requested(body.metrics)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    context = body.context.model_dump(exclude_none=True)
    if not (context.get("provided_context") or "").strip():
        raise HTTPException(
            status_code=400, detail="context.provided_context must be a non-empty string"
        )

    job = Job(
        id=new_job_id(),
        status=JobStatus.queued.value,
        github_url=body.github_url,
        metrics_requested=metrics,
        options=_options_to_dict(body),
        context=context,
    )
    store = JobStore()
    await asyncio.to_thread(store.create, job)
    await run_pipeline(job.id)
    return await get_job(job.id, wait_seconds=0)
