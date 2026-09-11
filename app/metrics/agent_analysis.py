from __future__ import annotations

from typing import Any

from app.analysis.agent_evidence import (
    collect_agent_evidence,
    evidence_needs_semantic,
    normalize_agent_judgment,
)
from app.analysis.context import get_analysis
from app.llm.reasoner import LLMReasoner, ReasoningRequest
from app.llm.selective import LlmPlan, build_evidence_pack, decide_llm_use
from app.metrics.ai_usage import scan_manifests
from app.metrics.base import Metric, MetricContext, MetricResult


def _payload(normalized: dict[str, Any], evidence: dict[str, Any], *, status: str = "ok") -> dict[str, Any]:
    return {
        **normalized,
        "status": status,
        "agent_evidence": {
            "items": evidence.get("items") or [],
            "framework_dependencies": evidence.get("framework_dependencies") or {},
            "graph_nodes": evidence.get("graph_nodes") or [],
            "graph_edges": evidence.get("graph_edges") or [],
            "tool_registrations": evidence.get("tool_registrations") or [],
            "orchestration_loops": evidence.get("orchestration_loops") or [],
            "needs_semantic": evidence.get("needs_semantic"),
            "naming_or_manifest_only": evidence.get("naming_or_manifest_only"),
            "note": evidence.get("note"),
        },
    }


class AgentAnalysisMetric(Metric):
    name = "agent_analysis"
    tier = "llm"
    description = (
        "Deterministic agent-graph evidence plus selective Gemini classification "
        "(wrapper vs tools vs genuine orchestration)."
    )
    depends_on = ["ai_usage"]
    default_options = {"max_agent_files_kb": 40, "max_files": 10}
    skippable_when = "LLM unavailable when semantic interpretation is required"
    output_schema = {
        "type": "object",
        "properties": {
            "classification": {
                "type": "string",
                "enum": [
                    "NO_AGENT",
                    "LLM_WRAPPER",
                    "LINEAR_CHAIN",
                    "TOOL_USING_LLM",
                    "WORKFLOW_ORCHESTRATION",
                    "GENUINE_AGENT_ORCHESTRATION",
                ],
            },
            "agent_count": {"type": "integer"},
            "agents": {"type": "array"},
            "has_real_orchestration": {"type": "boolean"},
            "evidence_ids": {"type": "array", "items": {"type": "string"}},
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

        facts = get_analysis(ctx).code_facts
        source_facts = getattr(facts, "source_facts", None) or []
        evidence = collect_agent_evidence(
            source_facts,
            manifests=ctx.snapshot.package_manifests,
            call_graph=getattr(facts, "call_graph", None),
            file_contents=ctx.snapshot.file_contents,
            ai_usage=ctx.prior_results.get("ai_usage") or {},
        )

        plan: LlmPlan | None = ctx.extras.get("llm_plan")
        if plan is None:
            plan = decide_llm_use(
                requested=["agent_analysis"],
                llm_enabled=bool(getattr(ctx.extras.get("llm_client"), "enabled", False)),
                static_metrics=ctx.prior_results,
                submission_context=ctx.extras.get("submission_context"),
                code_facts=facts,
                agent_deps=agent_deps,
            )

        precomputed = (ctx.extras.get("llm_judgment") or {}).get("agent_analysis")
        if precomputed:
            normalized = normalize_agent_judgment(precomputed, evidence)
            return MetricResult(name=self.name, status="ok", data=_payload(normalized, evidence))

        if "agent_analysis" not in plan.metrics and not evidence_needs_semantic(evidence):
            normalized = normalize_agent_judgment(
                {
                    "classification": "NO_AGENT",
                    "agent_count": 0,
                    "agents": [],
                    "confidence": "high",
                    "reasoning": (
                        "Deterministic: no imported agent framework usage, tool registration, "
                        "graph, invocation, or orchestration loop. A langchain/langgraph/agent "
                        "string in dependencies or README is not enough."
                    ),
                    "evidence_ids": [item["id"] for item in evidence.get("items") or []],
                },
                evidence,
            )
            return MetricResult(name=self.name, status="ok", data=_payload(normalized, evidence))

        llm: LLMReasoner | None = ctx.extras.get("llm_client")
        if not llm or not llm.enabled:
            normalized = normalize_agent_judgment(
                {
                    "classification": "NO_AGENT",
                    "confidence": "low",
                    "reasoning": "Vertex AI not configured; cannot run semantic agent_analysis.",
                },
                evidence,
            )
            return MetricResult(
                name=self.name,
                status="skipped",
                data=_payload(normalized, evidence, status="skipped"),
                reason="llm_not_configured",
            )

        pack = ctx.extras.get("llm_evidence_pack")
        if pack is None:
            pack = build_evidence_pack(
                code_facts=facts,
                snapshot=ctx.snapshot,
                static_metrics=ctx.prior_results,
                plan=plan,
            )
        pack = {**pack, "agent_evidence": evidence}
        judgment = await llm.reason(
            ReasoningRequest(
                metrics=["agent_analysis"],
                questions=plan.questions
                or [
                    "Classify as NO_AGENT, LLM_WRAPPER, LINEAR_CHAIN, TOOL_USING_LLM, WORKFLOW_ORCHESTRATION, or GENUINE_AGENT_ORCHESTRATION.",
                    "Cite evidence_ids. Do not classify as an agent merely because langchain, langgraph, or agent appears in dependencies or README.",
                ],
                evidence=pack,
                submission_context=ctx.extras.get("submission_context"),
                reasons=plan.reasons,
            )
        )
        section = judgment.get("agent_analysis") or judgment
        normalized = normalize_agent_judgment(section, evidence)
        return MetricResult(name=self.name, status="ok", data=_payload(normalized, evidence))
