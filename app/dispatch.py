"""Dispatch analysis to a Cloud Run Job, or run it inline when unset."""

from __future__ import annotations

import logging
from typing import Any

import google.auth
import httpx
from fastapi import BackgroundTasks, HTTPException
from google.auth.transport.requests import Request

from app.config import Settings, get_settings
from app.jobs import JobStatus, JobStore

logger = logging.getLogger(__name__)


async def dispatch_analysis_job(job_id: str, background: BackgroundTasks) -> None:
    """Start analysis off the HTTP request when a Cloud Run Job is configured."""
    settings = get_settings()
    if settings.use_cloud_run_jobs:
        try:
            await trigger_cloud_run_job(job_id, settings=settings)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to trigger Cloud Run Job for %s", job_id)
            store = JobStore()
            store.update(
                job_id,
                status=JobStatus.failed.value,
                error=f"worker_dispatch_failed: {exc}",
            )
            raise HTTPException(
                status_code=502,
                detail="Failed to start analysis worker",
            ) from exc
        return

    from app.pipeline.runner import run_pipeline

    background.add_task(run_pipeline, job_id)


async def trigger_cloud_run_job(job_id: str, *, settings: Settings | None = None) -> dict[str, Any]:
    """Execute the worker Cloud Run Job with ANALYSIS_JOB_ID for this Firestore job."""
    settings = settings or get_settings()
    name = settings.cloud_run_job_name
    if not name:
        raise RuntimeError("CLOUD_RUN_JOB_NAME is not set")
    project = settings.resolved_project_id
    if not project:
        raise RuntimeError("GOOGLE_CLOUD_PROJECT is required to trigger the worker job")
    location = settings.worker_job_location
    url = (
        f"https://run.googleapis.com/v2/projects/{project}/locations/{location}/jobs/{name}:run"
    )
    token = _access_token()
    body = {
        "overrides": {
            "containerOverrides": [
                {
                    "env": [{"name": "ANALYSIS_JOB_ID", "value": job_id}],
                }
            ]
        }
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            url,
            json=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        response.raise_for_status()
        return response.json() if response.content else {}


def _access_token() -> str:
    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    if not creds.valid or not creds.token:
        creds.refresh(Request())
    if not creds.token:
        raise RuntimeError("Could not obtain Google access token for Cloud Run Jobs")
    return str(creds.token)
