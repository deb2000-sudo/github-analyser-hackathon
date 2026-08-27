from __future__ import annotations

from typing import Any

from app.github.client import PATH_HINTS, paths_matching
from app.llm.client import LLMClient
from app.metrics.ai_usage import scan_manifests
from app.metrics.solution_fit import _curate_paths
from app.metrics.base import Metric, MetricContext, MetricResult
from app.pipeline.prompt import build_system_prompt, build_user_prompt


class AgentAnalysisMetric(Metric):
    name = "agent_analysis"
    tier = "llm"
    description = (
        "LLM analysis of agent/orchestration patterns in the repo. Always runs when LLM is enabled."
    )
    depends_on = ["ai_usage"]
    default_options = {"max_agent_files_kb": 40, "max_files": 10}
    skippable_when = "LLM unavailable or no readable source files"
    output_schema = {
        "type": "object",
        "properties": {
            "agent_count": {"type": "integer"},
            "agents": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "role_guess": {"type": "string"},
                        "file": {"type": "string"},
                        "evidence": {"type": "string"},
                    },
                },
            },
            "has_real_orchestration": {"type": "boolean"},
            "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
            "reasoning": {"type": "string"},
            "status": {"type": "string"},
        },
    }

    async def run(self, ctx: MetricContext) -> MetricResult:
        opts = {**self.default_options, **ctx.options}

        agent_deps = ctx.extras.get("agent_frameworks_found")
        if agent_deps is None:
            # ai_usage wasn't requested — run static scan anyway
            _, agent_deps = scan_manifests(ctx.snapshot.package_manifests)
            ctx.extras["agent_frameworks_found"] = agent_deps
            ai_deps, _ = scan_manifests(ctx.snapshot.package_manifests)
            ctx.extras.setdefault("ai_dependencies_found", ai_deps)

        llm: LLMClient | None = ctx.extras.get("llm_client")
        if not llm or not llm.enabled:
            return MetricResult(
                name=self.name,
                status="skipped",
                data={
                    "status": "skipped",
                    "agent_count": 0,
                    "agents": [],
                    "has_real_orchestration": False,
                    "confidence": "low",
                    "reasoning": "Vertex AI not configured; cannot run agent_analysis.",
                },
                reason="llm_not_configured",
            )

        candidates = paths_matching(ctx.snapshot.tree, PATH_HINTS)
        candidates = [
            p
            for p in candidates
            if p.endswith((".py", ".ts", ".tsx", ".js", ".jsx", ".mjs"))
            and "node_modules" not in p
            and ".venv" not in p
        ][: int(opts.get("max_files", 15))]
        if not candidates:
            candidates = _curate_paths(ctx.snapshot.tree, max_files=int(opts.get("max_files", 15)))

        if not candidates:
            return MetricResult(
                name=self.name,
                status="skipped",
                data={
                    "status": "skipped",
                    "agent_count": 0,
                    "agents": [],
                    "has_real_orchestration": False,
                    "confidence": "medium",
                    "reasoning": "No agent-related or entry source files found.",
                },
                reason="no_agent_files",
            )

        gh = ctx.extras.get("github_client")
        max_kb = int(opts.get("max_agent_files_kb", 40))
        if gh and not ctx.extras.get("skip_file_fetch"):
            await gh.fetch_files(ctx.snapshot, candidates, max_file_kb=max_kb)

        files = {p: ctx.snapshot.file_contents[p] for p in candidates if p in ctx.snapshot.file_contents}
        if not files:
            return MetricResult(
                name=self.name,
                status="skipped",
                data={
                    "status": "skipped",
                    "agent_count": 0,
                    "agents": [],
                    "has_real_orchestration": False,
                    "confidence": "low",
                    "reasoning": "Could not fetch agent-related file contents.",
                },
                reason="fetch_failed",
            )

        precomputed = (ctx.extras.get("llm_judgment") or {}).get("agent_analysis")
        if precomputed:
            section = precomputed
            return MetricResult(
                name=self.name,
                status="ok",
                data={
                    "agent_count": int(section.get("agent_count") or len(section.get("agents") or [])),
                    "agents": section.get("agents") or [],
                    "has_real_orchestration": bool(section.get("has_real_orchestration", False)),
                    "confidence": section.get("confidence", "low"),
                    "reasoning": section.get("reasoning"),
                    "status": "ok",
                },
            )

        system = build_system_prompt(["agent_analysis"])
        user = build_user_prompt(
            metrics=["agent_analysis"],
            files=files,
            hints={
                "agent_frameworks_found": agent_deps or [],
                "ai_dependencies_found": ctx.extras.get("ai_dependencies_found", []),
            },
            submission_context=ctx.extras.get("submission_context"),
        )
        judgment = await llm.judge_json(system=system, user=user)
        section = judgment.get("agent_analysis") or judgment

        data: dict[str, Any] = {
            "agent_count": int(section.get("agent_count") or len(section.get("agents") or [])),
            "agents": section.get("agents") or [],
            "has_real_orchestration": bool(section.get("has_real_orchestration", False)),
            "confidence": section.get("confidence", "low"),
            "reasoning": section.get("reasoning"),
            "status": "ok",
        }
        return MetricResult(name=self.name, status="ok", data=data)
