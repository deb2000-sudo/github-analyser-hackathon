"""Phase 18: job claim/complete is idempotent; retries cannot overwrite a finished report."""

from __future__ import annotations

import asyncio
from app.jobs import JobStatus, can_complete, claim_outcome
from app.pipeline.runner import run_pipeline, worker_execution_id
from app.worker import resolve_job_id


def test_status_enum_covers_failure_states():
    assert {s.value for s in JobStatus} == {"queued", "running", "succeeded", "failed"}


def test_claim_skips_terminal_statuses():
    assert claim_outcome("queued") == "claim"
    assert claim_outcome("running") == "claim"
    assert claim_outcome("succeeded") == "already_terminal"
    assert claim_outcome("failed") == "already_terminal"


def test_complete_refuses_to_overwrite_finished_report():
    assert can_complete("succeeded", "exec-a", "exec-a") is False
    assert can_complete("failed", "exec-a", "exec-b") is False
    assert can_complete("running", "exec-a", "exec-a") is True
    assert can_complete("running", "exec-a", "exec-b") is False
    assert can_complete("queued", None, "exec-a") is True


def test_run_pipeline_does_not_reload_terminal_job(monkeypatch):
    class FakeStore:
        def claim(self, job_id: str, execution_id: str) -> str:
            assert job_id == "donejob"
            assert execution_id == "exec-1"
            return "already_terminal"

        def get(self, job_id: str):
            raise AssertionError("finished jobs must not be analyzed again")

        def complete(self, *args, **kwargs):
            raise AssertionError("must not write a second report")

    monkeypatch.setattr("app.pipeline.runner.JobStore", FakeStore)
    asyncio.run(run_pipeline("donejob", execution_id="exec-1"))


def test_worker_execution_id_prefers_cloud_run_execution(monkeypatch):
    monkeypatch.setenv("CLOUD_RUN_EXECUTION", "github-analyser-worker-abc")
    monkeypatch.delenv("ANALYSIS_EXECUTION_ID", raising=False)
    assert worker_execution_id() == "github-analyser-worker-abc"


def test_resolve_job_id_from_env_and_argv(monkeypatch):
    monkeypatch.setenv("ANALYSIS_JOB_ID", "from-env")
    assert resolve_job_id(["from-argv"]) == "from-env"
    monkeypatch.delenv("ANALYSIS_JOB_ID", raising=False)
    assert resolve_job_id(["from-argv"]) == "from-argv"
    assert resolve_job_id([]) == ""
