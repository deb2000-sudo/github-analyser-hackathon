"""Phase 17: one AnalysisContext per analysis; repository parsing happens once."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.analysis.context import (
    TIMING_KEYS,
    AnalysisContext,
    build_analysis_context,
    get_analysis,
)
from app.analysis.extract import extract_source_facts
from app.analysis.facts import build_code_facts
from app.github.client import RepoRef, RepoSnapshot
from app.github.validation import RepoAccessInfo
from app.metrics.agent_analysis import AgentAnalysisMetric
from app.metrics.ai_usage import AiUsageMetric
from app.metrics.base import MetricContext
from app.metrics.frontend_backend import FrontendBackendMetric
from app.metrics.fullstack import FullstackMetric
from app.pipeline.analyze import analyze_snapshot

_SOURCE = {
    "backend/main.py": '''
from fastapi import FastAPI
from openai import OpenAI

app = FastAPI()

def generate_ai(prompt):
    client = OpenAI()
    return client.responses.create(prompt)

@app.post("/api/chat")
def chat(body):
    result = generate_ai(body.message)
    return {"answer": result}
''',
    "frontend/src/Chat.tsx": 'fetch("/api/chat", { method: "POST" });\n',
    "backend/requirements.txt": "fastapi\nopenai\n",
    "frontend/package.json": '{"dependencies":{"react":"18.3.0"}}',
}


def _snapshot() -> RepoSnapshot:
    return RepoSnapshot(
        ref=RepoRef("o", "r", commit_sha="abc"),
        tree=[{"path": path, "type": "blob"} for path in _SOURCE],
        file_contents=dict(_SOURCE),
        package_manifests={
            "backend/requirements.txt": _SOURCE["backend/requirements.txt"],
            "frontend/package.json": _SOURCE["frontend/package.json"],
        },
        commits=[{"commit": {"author": {"date": "2026-01-01T00:00:00Z"}}}],
    )


def _run_static_metrics(ctx: MetricContext) -> None:
    async def _go() -> None:
        for metric in (FullstackMetric(), FrontendBackendMetric(), AiUsageMetric(), AgentAnalysisMetric()):
            result = await metric.run(ctx)
            ctx.prior_results[metric.name] = {"status": result.status, **(result.data or {})}

    asyncio.run(_go())


def test_analysis_context_contains_shared_fields():
    analysis = build_analysis_context(_snapshot())
    assert analysis.repository["full_name"] == "o/r"
    assert analysis.inventory["file_count"] >= 1
    assert analysis.manifests
    assert analysis.code_facts.source_facts
    assert analysis.routes
    assert analysis.http_calls
    assert analysis.call_graph.graph.number_of_nodes() >= 1
    assert analysis.data_flow.graph.number_of_nodes() >= 1
    assert isinstance(analysis.ai_findings, list)
    assert isinstance(analysis.evidence, list)
    for key in TIMING_KEYS:
        assert key in analysis.timings
        assert analysis.timings[key] >= 0


def test_extract_source_facts_once_across_metrics():
    snapshot = _snapshot()
    with patch("app.analysis.facts.extract_source_facts", wraps=extract_source_facts) as parse:
        ctx = MetricContext(snapshot=snapshot, extras={"llm_client": SimpleNamespace(enabled=False)})
        _run_static_metrics(ctx)
        assert parse.call_count == 1
        assert ctx.analysis is not None
        assert ctx.extras["analysis_context"] is ctx.analysis


def test_metrics_do_not_rebuild_code_facts_when_context_exists():
    snapshot = _snapshot()
    analysis = build_analysis_context(snapshot)
    gh = AsyncMock()
    ctx = MetricContext(
        snapshot=snapshot,
        analysis=analysis,
        extras={
            "analysis_context": analysis,
            "code_facts": analysis.code_facts,
            "skip_file_fetch": False,
            "github_client": gh,
            "llm_client": SimpleNamespace(enabled=False),
        },
    )
    with patch("app.analysis.facts.build_code_facts", wraps=build_code_facts) as rebuilt:
        _run_static_metrics(ctx)
        rebuilt.assert_not_called()
    gh.fetch_files.assert_not_called()
    gh.fetch_snapshot.assert_not_called()


def test_get_analysis_is_stable_on_the_same_context():
    snapshot = _snapshot()
    ctx = MetricContext(snapshot=snapshot)
    first = get_analysis(ctx)
    second = get_analysis(ctx)
    assert first is second


def test_analyze_snapshot_records_stage_timings():
    snapshot = _snapshot()
    access = RepoAccessInfo(
        owner="o",
        name="r",
        is_public=True,
        exists=True,
        default_branch="main",
    )
    with patch("app.analysis.facts.extract_source_facts", wraps=extract_source_facts) as parse:
        result = asyncio.run(
            analyze_snapshot(
                snapshot,
                access=access,
                github_url="https://github.com/o/r",
                requested=["fullstack", "frontend_backend", "ai_usage", "agent_analysis", "solution_fit"],
                options={},
                submission_context={"provided_context": "A chat app that uses an LLM."},
                request_scoring=None,
                llm=SimpleNamespace(enabled=False),
                fetch_ms=4,
            )
        )
        assert parse.call_count == 1
    timing = result["metadata"]["timing"]
    for key in TIMING_KEYS:
        assert key in timing
        assert isinstance(timing[key], int)
        assert timing[key] >= 0
    assert timing["fetch_ms"] == 4
    assert result["architecture"]["application_type"]
    assert result["ai"]["model_invocation_detected"] is True


def test_metrics_do_not_fetch_when_github_client_is_present():
    snapshot = _snapshot()
    analysis = build_analysis_context(snapshot)
    gh = MagicMock()
    gh.fetch_files = AsyncMock()
    ctx = MetricContext(
        snapshot=snapshot,
        extras={
            "github_client": gh,
            "llm_client": SimpleNamespace(enabled=False),
        },
    )
    from app.analysis.context import attach_analysis

    attach_analysis(ctx, analysis)
    asyncio.run(FullstackMetric().run(ctx))
    asyncio.run(AiUsageMetric().run(ctx))
    asyncio.run(AgentAnalysisMetric().run(ctx))
    gh.fetch_files.assert_not_called()
