"""Phase 18: API dispatches a Cloud Run Job, or runs inline when unset."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.config import Settings, get_settings
from app.dispatch import dispatch_analysis_job, trigger_cloud_run_job
from app.jobs import JobStatus
from app.worker import main, resolve_job_id


def test_dispatch_runs_inline_when_job_name_unset(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.delenv("CLOUD_RUN_JOB_NAME", raising=False)
    settings = Settings(_env_file=None)
    assert settings.use_cloud_run_jobs is False
    monkeypatch.setattr("app.dispatch.get_settings", lambda: settings)

    background = MagicMock()
    added = []

    async def fake_pipeline(job_id: str) -> None:
        added.append(job_id)

    monkeypatch.setattr("app.pipeline.runner.run_pipeline", fake_pipeline)

    asyncio.run(dispatch_analysis_job("job123", background))
    background.add_task.assert_called_once()
    assert background.add_task.call_args.args[1] == "job123"
    get_settings.cache_clear()


def test_dispatch_triggers_cloud_run_job_when_configured(monkeypatch):
    get_settings.cache_clear()
    settings = Settings(
        _env_file=None,
        cloud_run_job_name="github-analyser-worker",
        gcp_project_id="demo-proj",
        gcp_location="us-central1",
    )
    monkeypatch.setattr("app.dispatch.get_settings", lambda: settings)
    trigger = AsyncMock(return_value={"name": "executions/1"})
    monkeypatch.setattr("app.dispatch.trigger_cloud_run_job", trigger)
    background = MagicMock()
    asyncio.run(dispatch_analysis_job("job123", background))
    trigger.assert_awaited_once()
    background.add_task.assert_not_called()
    get_settings.cache_clear()


def test_dispatch_marks_failed_when_trigger_fails(monkeypatch):
    get_settings.cache_clear()
    settings = Settings(
        _env_file=None,
        cloud_run_job_name="github-analyser-worker",
        gcp_project_id="demo-proj",
    )
    monkeypatch.setattr("app.dispatch.get_settings", lambda: settings)
    monkeypatch.setattr(
        "app.dispatch.trigger_cloud_run_job",
        AsyncMock(side_effect=RuntimeError("no permission")),
    )
    store = MagicMock()
    monkeypatch.setattr("app.dispatch.JobStore", lambda: store)

    with pytest.raises(Exception) as excinfo:
        asyncio.run(dispatch_analysis_job("job123", MagicMock()))
    assert getattr(excinfo.value, "status_code", None) == 502
    store.update.assert_called_once()
    kwargs = store.update.call_args.kwargs
    assert kwargs["status"] == JobStatus.failed.value
    assert "worker_dispatch_failed" in kwargs["error"]
    get_settings.cache_clear()


def test_trigger_cloud_run_job_posts_job_id(monkeypatch):
    settings = Settings(
        _env_file=None,
        cloud_run_job_name="github-analyser-worker",
        gcp_project_id="demo-proj",
        gcp_location="us-central1",
    )
    monkeypatch.setattr("app.dispatch._access_token", lambda: "token")

    captured: dict = {}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json=None, headers=None):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            request = httpx.Request("POST", url)
            return httpx.Response(200, json={"name": "ok"}, request=request)

    monkeypatch.setattr("app.dispatch.httpx.AsyncClient", FakeClient)
    payload = asyncio.run(trigger_cloud_run_job("abc123", settings=settings))
    assert payload["name"] == "ok"
    assert "jobs/github-analyser-worker:run" in captured["url"]
    env = captured["json"]["overrides"]["containerOverrides"][0]["env"]
    assert {"name": "ANALYSIS_JOB_ID", "value": "abc123"} in env


def test_worker_main_requires_job_id(monkeypatch):
    monkeypatch.delenv("ANALYSIS_JOB_ID", raising=False)
    monkeypatch.setattr("app.worker.resolve_job_id", lambda: "")
    with pytest.raises(SystemExit) as excinfo:
        main()
    assert excinfo.value.code == 2
    assert resolve_job_id([]) == ""
