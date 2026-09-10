from __future__ import annotations

from typing import Any

from app.llm.reasoner import LLMReasoner, ReasoningRequest
from app.llm.selective import LlmPlan, build_evidence_pack, decide_llm_use
from app.metrics.ai_usage import scan_manifests
from app.metrics.base import Metric, MetricContext, MetricResult


def _empty_agents(*, reasoning: str, status: str = "ok", confidence: str = "high") -> dict[str, Any]:
    return {
        "status": status,
        "agent_count": 0,
        "agents": [],
        "has_real_orchestration": False,
        "confidence": confidence,
        "reasoning": reasoning,
    }


class AgentAnalysisMetric(Metric):
    name = "agent_analysis"
    tier = "llm"
    description = (
        "Semantic judgment of agent/orchestration vs a simple LLM wrapper. "
        "Runs Gemini only when interpretation is required."
    )
    depends_on = ["ai_usage"]
    default_options = {"max_agent_files_kb": 40, "max_files": 10}
    skippable_when = "LLM unavailable when semantic interpretation is required"
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
        agent_deps = ctx.extras.get("agent_frameworks_found")
        if agent_deps is None:
            _, agent_deps = scan_manifests(ctx.snapshot.package_manifests)
            ctx.extras["agent_frameworks_found"] = agent_deps
            ai_deps, _ = scan_manifests(ctx.snapshot.package_manifests)
            ctx.extras.setdefault("ai_dependencies_found", ai_deps)

        plan: LlmPlan | None = ctx.extras.get("llm_plan")
        if plan is None:
            plan = decide_llm_use(
                requested=["agent_analysis"],
                llm_enabled=bool(getattr(ctx.extras.get("llm_client"), "enabled", False)),
                static_metrics=ctx.prior_results,
                submission_context=ctx.extras.get("submission_context"),
                code_facts=ctx.extras.get("code_facts"),
                agent_deps=agent_deps,
            )

        precomputed = (ctx.extras.get("llm_judgment") or {}).get("agent_analysis")
        if precomputed:
            return MetricResult(
                name=self.name,
                status="ok",
                data={
                    "agent_count": int(precomputed.get("agent_count") or len(precomputed.get("agents") or [])),
                    "agents": precomputed.get("agents") or [],
                    "has_real_orchestration": bool(precomputed.get("has_real_orchestration", False)),
                    "confidence": precomputed.get("confidence", "low"),
                    "reasoning": precomputed.get("reasoning"),
                    "status": "ok",
                },
            )

        if "agent_analysis" not in plan.metrics:
            return MetricResult(
                name=self.name,
                status="ok",
                data=_empty_agents(
                    reasoning=(
                        "Deterministic: no agent framework, orchestration pattern, "
                        "invocation, conflict, or low-confidence finding requiring "
                        "semantic review."
                    )
                ),
            )

        llm: LLMReasoner | None = ctx.extras.get("llm_client")
        if not llm or not llm.enabled:
            return MetricResult(
                name=self.name,
                status="skipped",
                data=_empty_agents(
                    reasoning="Vertex AI not configured; cannot run semantic agent_analysis.",
                    status="skipped",
                    confidence="low",
                ),
                reason="llm_not_configured",
            )

        pack = ctx.extras.get("llm_evidence_pack")
        if pack is None:
            pack = build_evidence_pack(
                code_facts=ctx.extras.get("code_facts"),
                snapshot=ctx.snapshot,
                static_metrics=ctx.prior_results,
                plan=plan,
            )
        judgment = await llm.reason(
            ReasoningRequest(
                metrics=["agent_analysis"],
                questions=plan.questions or [
                    "Is this meaningful agent orchestration?",
                    "Is this just a simple LLM wrapper?",
                    "Are the extracted components semantically being used as agents/tools/workflows?",
                ],
                evidence=pack,
                submission_context=ctx.extras.get("submission_context"),
                reasons=plan.reasons,
            )
        )
        section = judgment.get("agent_analysis") or judgment
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
