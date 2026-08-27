"""Lightweight unit tests for static metric helpers (no network)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json

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
    assert "@langchain/core" in ai


def test_scan_manifests_no_false_positive_scoped_core():
    """@babel/core must not match @langchain/core via basename fuzzy matching."""
    manifests = {
        "package.json": '{"dependencies":{"@babel/core":"7.0.0","react":"18.0.0"}}',
    }
    ai, agents = scan_manifests(manifests)
    assert ai == []
    assert agents == []


def test_agent_framework_requires_code_import():
    from app.metrics.ai_imports import reconcile_ai_dependencies

    reconciled = reconcile_ai_dependencies(
        ["@langchain/core"],
        {},
    )
    assert reconciled["ai_dependencies_found"] == []
    assert "@langchain/core" in reconciled["rejected_false_manifest_deps"]


def test_agent_framework_verified_with_import():
    from app.metrics.ai_imports import reconcile_ai_dependencies

    reconciled = reconcile_ai_dependencies(
        ["langchain"],
        {"langchain": ["backend/agent.py"]},
    )
    assert "langchain" in reconciled["ai_dependencies_found"]
    assert "langchain" in reconciled["agent_frameworks_found"]


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
    assert result.data["repo_type"] == "fullstack"
    assert result.data["frontend_detected"]["stack_guess"] == "React"
    assert result.data["backend_detected"]["stack_guess"] == "FastAPI"


def test_fullstack_vite_react_with_src_api_is_frontend_only():
    """Folder names like src/api must not be treated as a server."""
    tree = [
        {"path": "package.json", "type": "blob"},
        {"path": "index.html", "type": "blob"},
        {"path": "vite.config.js", "type": "blob"},
        {"path": "vercel.json", "type": "blob"},
        {"path": "src/App.jsx", "type": "blob"},
        {"path": "src/main.jsx", "type": "blob"},
        {"path": "src/api/client.js", "type": "blob"},
        {"path": "src/api/hackathon.js", "type": "blob"},
        {"path": "public/favicon.ico", "type": "blob"},
    ]
    snapshot = RepoSnapshot(
        ref=RepoRef("o", "hackniat"),
        tree=tree,
        package_manifests={
            "package.json": json.dumps(
                {
                    "dependencies": {"react": "18.3.0", "react-dom": "18.3.0"},
                    "devDependencies": {"vite": "5.4.0", "@vitejs/plugin-react": "4.3.0"},
                }
            )
        },
        file_contents={
            "vite.config.js": "import { defineConfig } from 'vite'\nexport default defineConfig({})",
            "src/api/client.js": "export async function getMe() { return fetch('/api/me') }\n",
            "index.html": "<div id='root'></div>",
        },
    )
    result = asyncio.run(FullstackMetric().run(MetricContext(snapshot=snapshot)))
    assert result.data["frontend_detected"]["present"] is True
    assert result.data["frontend_detected"]["stack_guess"] == "React"
    assert result.data["backend_detected"]["present"] is False
    assert result.data["backend_detected"]["stack_guess"] is None
    assert result.data["is_fullstack"] is False
    assert result.data["repo_type"] == "frontend"


def test_fullstack_express_plus_react_is_fullstack():
    tree = [
        {"path": "package.json", "type": "blob"},
        {"path": "index.html", "type": "blob"},
        {"path": "src/App.jsx", "type": "blob"},
        {"path": "server.js", "type": "blob"},
    ]
    snapshot = RepoSnapshot(
        ref=RepoRef("o", "r"),
        tree=tree,
        package_manifests={
            "package.json": json.dumps(
                {"dependencies": {"react": "18.0.0", "express": "4.19.0"}}
            )
        },
        file_contents={
            "server.js": "const express = require('express');\nconst app = express();\napp.listen(3000);\n",
        },
    )
    result = asyncio.run(FullstackMetric().run(MetricContext(snapshot=snapshot)))
    assert result.data["is_fullstack"] is True
    assert result.data["repo_type"] == "fullstack"
    assert result.data["backend_detected"]["stack_guess"] == "Express"


def test_fullstack_next_api_route_counts_as_backend():
    tree = [
        {"path": "package.json", "type": "blob"},
        {"path": "next.config.js", "type": "blob"},
        {"path": "app/page.tsx", "type": "blob"},
        {"path": "app/api/hello/route.ts", "type": "blob"},
    ]
    snapshot = RepoSnapshot(
        ref=RepoRef("o", "r"),
        tree=tree,
        package_manifests={"package.json": json.dumps({"dependencies": {"next": "14.0.0", "react": "18.0.0"}})},
        file_contents={
            "app/api/hello/route.ts": "export async function GET() { return Response.json({ ok: true }) }\n",
        },
    )
    result = asyncio.run(FullstackMetric().run(MetricContext(snapshot=snapshot)))
    assert result.data["frontend_detected"]["present"] is True
    assert result.data["backend_detected"]["present"] is True
    assert result.data["repo_type"] == "fullstack"


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
            "provided_context": (
                "This project is a multi-agent LangGraph study planner that helps students "
                "build personalized study schedules using RAG over course materials."
            ),
            "rubrics": ["Uses LLM"],
        },
    )
    assert "LangGraph study planner" in user
    assert "RAG over course materials" in user
    assert "Uses LLM" in user


def test_solution_fit_skips_without_context():
    from app.metrics.solution_fit import SolutionFitMetric

    snapshot = RepoSnapshot(ref=RepoRef("o", "r"), tree=[])
    result = asyncio.run(
        SolutionFitMetric().run(MetricContext(snapshot=snapshot, extras={}))
    )
    assert result.status == "skipped"
    assert result.reason == "missing_submission_context"
