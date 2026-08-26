"""Lightweight unit tests for static metric helpers (no network)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from app.github.client import RepoRef, RepoSnapshot
from app.metrics.ai_usage import AiUsageMetric, scan_manifests
from app.metrics.base import MetricContext
from app.metrics.fullstack import FullstackMetric
from app.metrics.repo_health import RepoHealthMetric
from app.pipeline.prompt import build_system_prompt


def test_scan_manifests_finds_ai_and_agent_deps():
    manifests = {
        "backend/requirements.txt": "fastapi==0.115.0\nopenai==1.0.0\nlangchain==0.2.0\n",
        "frontend/package.json": '{"dependencies":{"react":"18.0.0","@langchain/core":"0.2.0"}}',
    }
    ai, agents = scan_manifests(manifests)
    assert "openai" in ai
    assert "langchain" in ai
    assert "langchain" in agents
    assert "@langchain/core" in agents or "langchain" in agents


def test_fullstack_detects_react_fastapi():
    tree = [
        {"path": "frontend/package.json", "type": "blob"},
        {"path": "frontend/src/App.tsx", "type": "blob"},
        {"path": "frontend/public/index.html", "type": "blob"},
        {"path": "backend/requirements.txt", "type": "blob"},
        {"path": "backend/main.py", "type": "blob"},
    ]
    snapshot = RepoSnapshot(
        ref=RepoRef("o", "r"),
        tree=tree,
        package_manifests={
            "frontend/package.json": '{"dependencies":{"react":"18.3.0"}}',
            "backend/requirements.txt": "fastapi\nuvicorn\n",
        },
    )
    result = asyncio.run(FullstackMetric().run(MetricContext(snapshot=snapshot)))
    assert result.status == "ok"
    assert result.data["is_fullstack"] is True
    assert result.data["frontend_detected"]["stack_guess"] == "React"
    assert result.data["backend_detected"]["stack_guess"] == "FastAPI"


def test_repo_health_flags_dump():
    now = datetime.now(timezone.utc)
    commits = []
    for i in range(10):
        commits.append(
            {
                "commit": {
                    "author": {
                        "date": (now - timedelta(minutes=i)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "email": "a@b.c",
                    }
                },
                "author": {"login": "alice"},
            }
        )
    snapshot = RepoSnapshot(ref=RepoRef("o", "r"), commits=commits, contributors=[{"login": "alice"}])
    result = asyncio.run(RepoHealthMetric().run(MetricContext(snapshot=snapshot)))
    assert result.data["commit_count"] == 10
    assert result.data["flag_single_dump"] is True
    assert result.data["contributors"] == 1


def test_ai_usage_skips_llm_when_no_deps():
    snapshot = RepoSnapshot(
        ref=RepoRef("o", "r"),
        package_manifests={"requirements.txt": "flask==3.0.0\n"},
    )
    result = asyncio.run(AiUsageMetric().run(MetricContext(snapshot=snapshot)))
    assert result.data["ai_integration_type"] == "none"
    assert result.data["ai_dependencies_found"] == []


def test_prompt_only_includes_requested_sections():
    prompt = build_system_prompt(["ai_usage"])
    assert "ai_usage" in prompt
    assert "agent_analysis" not in prompt
    both = build_system_prompt(["ai_usage", "agent_analysis"])
    assert "ai_usage" in both and "agent_analysis" in both


def test_user_prompt_includes_submission_context():
    from app.pipeline.prompt import build_user_prompt

    user = build_user_prompt(
        metrics=["solution_fit"],
        files={"README.md": "# demo"},
        submission_context={
            "problem_statement": "Build a study planner agent",
            "solution_description": "LangGraph multi-agent",
            "rubrics": ["Uses LLM"],
        },
    )
    assert "Build a study planner agent" in user
    assert "LangGraph multi-agent" in user
    assert "Uses LLM" in user


def test_solution_fit_skips_without_context():
    from app.metrics.solution_fit import SolutionFitMetric

    snapshot = RepoSnapshot(ref=RepoRef("o", "r"), tree=[])
    result = asyncio.run(
        SolutionFitMetric().run(MetricContext(snapshot=snapshot, extras={}))
    )
    assert result.status == "skipped"
    assert result.reason == "missing_submission_context"
