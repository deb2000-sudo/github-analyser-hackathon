from __future__ import annotations

from typing import Any

from app.analysis.ai_evidence import (
    LEVEL_SDK,
    AiEvidence,
    confidence_from_level,
    detect_ai_evidence,
    integration_type_from_evidence,
    llm_providers_from_evidence,
)
from app.analysis.ai_fake import detect_fake_ai
from app.analysis.ai_flow import analyze_ai_flow
from app.analysis.facts import build_code_facts
from app.metrics.ai_imports import reconcile_ai_dependencies, scan_manifests
from app.metrics.ai_packages import is_agent_framework
from app.metrics.base import Metric, MetricContext, MetricResult

CONFIDENCE_RANK = {"low": 1, "medium": 2, "high": 3}


class AiUsageMetric(Metric):
    name = "ai_usage"
    tier = "static"
    description = (
        "Detects AI providers, verifies user→model and model→sink flow, and "
        "flags implementations that appear AI-powered but do not meaningfully use AI."
    )
    default_options = {"min_confidence": "medium", "max_evidence_files": 12}
    skippable_when = "no AI mention, dependency, import, or invocation"
    output_schema = {
        "type": "object",
        "properties": {
            "detected": {"type": "boolean"},
            "providers": {"type": "array", "items": {"type": "string"}},
            "integration_level": {"type": "integer"},
            "dependency_detected": {"type": "boolean"},
            "import_detected": {"type": "boolean"},
            "client_detected": {"type": "boolean"},
            "model_invocation_detected": {"type": "boolean"},
            "user_input_reaches_model": {"type": "boolean"},
            "model_output_used": {"type": "boolean"},
            "input_flow_evidence": {"type": "array"},
            "output_flow_evidence": {"type": "array"},
            "ai_verification": {"type": "object"},
            "ai_findings": {"type": "array"},
            "ai_dependencies_found": {"type": "array", "items": {"type": "string"}},
            "ai_integration_type": {"type": "string", "enum": ["none", "wrapper", "rag", "agentic"]},
            "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
            "evidence_files": {"type": "array", "items": {"type": "string"}},
            "agent_frameworks_found": {"type": "array", "items": {"type": "string"}},
            "manifest_only_deps": {"type": "array"},
            "rejected_false_manifest_deps": {"type": "array"},
            "code_evidence_by_package": {"type": "object"},
            "llm_providers": {"type": "object"},
            "reasoning": {"type": "string"},
        },
    }

    async def run(self, ctx: MetricContext) -> MetricResult:
        facts = ctx.extras.get("code_facts")
        if facts is None:
            facts = build_code_facts(ctx.snapshot)

        raw_manifest_deps, _ = scan_manifests(ctx.snapshot.package_manifests)
        evidence = detect_ai_evidence(
            getattr(facts, "source_facts", None) or [],
            manifests=ctx.snapshot.package_manifests,
            parsed_manifests=getattr(facts, "manifests", None),
            file_contents=ctx.snapshot.file_contents,
            submission_context=ctx.extras.get("submission_context"),
        )

        code_hits = _code_hits_from_evidence(evidence)
        reconciled = reconcile_ai_dependencies(raw_manifest_deps, code_hits)
        imported_agents = [
            pkg for pkg in evidence.imported_packages if is_agent_framework(pkg)
        ]
        agent_deps = imported_agents or [
            pkg for pkg in reconciled["agent_frameworks_found"] if pkg in evidence.imported_packages
        ]
        verified_deps = list(dict.fromkeys([*evidence.packages, *reconciled["ai_dependencies_found"]]))

        ctx.extras["ai_dependencies_found"] = verified_deps
        ctx.extras["agent_frameworks_found"] = agent_deps

        integration = integration_type_from_evidence(evidence)
        precomputed = (ctx.extras.get("llm_judgment") or {}).get("ai_usage")
        if precomputed and evidence.integration_level >= LEVEL_SDK:
            guessed = str(precomputed.get("ai_integration_type") or "").lower()
            if guessed in {"wrapper", "rag", "agentic"}:
                if guessed == "agentic" and not agent_deps:
                    pass
                else:
                    integration = guessed

        llm_providers = llm_providers_from_evidence(evidence)
        verification = analyze_ai_flow(
            getattr(facts, "source_facts", None) or [],
            getattr(facts, "call_graph", None),
            getattr(facts, "data_flow", None),
        )
        findings = detect_fake_ai(
            getattr(facts, "source_facts", None) or [],
            evidence=evidence,
            verification=verification,
            call_graph=getattr(facts, "call_graph", None),
            manifests=ctx.snapshot.package_manifests,
        )
        diagnostics = {
            "manifest_deps_raw": reconciled["manifest_deps_raw"],
            "manifest_only_deps": reconciled["manifest_only_deps"],
            "rejected_false_manifest_deps": reconciled["rejected_false_manifest_deps"],
            "code_evidence_by_package": reconciled["code_evidence_by_package"],
        }
        evidence_files = evidence.evidence_files[: int(ctx.options.get("max_evidence_files") or 12)]
        reasoning = _reasoning(evidence, llm_providers, verification, findings)

        return MetricResult(
            name=self.name,
            status="ok",
            data={
                **evidence.to_public_dict(),
                "ai_dependencies_found": verified_deps,
                "ai_integration_type": integration,
                "confidence": confidence_from_level(evidence.integration_level),
                "evidence_files": evidence_files,
                "agent_frameworks_found": agent_deps,
                "llm_providers": llm_providers,
                "reasoning": reasoning,
                "user_input_reaches_model": verification["user_input_reaches_model"],
                "model_output_used": verification["model_output_used"],
                "input_flow_evidence": verification["input_flow_evidence"],
                "output_flow_evidence": verification["output_flow_evidence"],
                "ai_verification": verification,
                "ai_findings": findings,
                **diagnostics,
            },
        )


def _code_hits_from_evidence(evidence: AiEvidence) -> dict[str, list[str]]:
    hits: dict[str, list[str]] = {}
    for ev in evidence.by_provider.values():
        if not ev.imported and not ev.client and not ev.invocation:
            continue
        for pkg in ev.packages:
            hits.setdefault(pkg, [])
            for path in ev.files:
                if path not in hits[pkg]:
                    hits[pkg].append(path)
    return hits


def _reasoning(
    evidence: AiEvidence,
    llm_providers: dict[str, Any],
    verification: dict[str, Any] | None = None,
    findings: list[dict[str, Any]] | None = None,
) -> str:
    verification = verification or {}
    failed = [f.get("rule_id") for f in (findings or []) if f.get("status") == "failed"]
    if evidence.integration_level == 0:
        base = "No AI mention, dependency, SDK import, or model invocation in scanned facts."
    elif evidence.integration_level == 1:
        names = ", ".join(evidence.providers) or "AI"
        base = f"Level 1: {names} mentioned in README/UI/context only."
    elif evidence.integration_level == 2:
        base = (
            "Level 2: AI dependency declared "
            f"({', '.join(evidence.packages[:5]) or 'unknown'}) but no SDK import or invocation."
        )
    elif evidence.integration_level == 3:
        kind = "client" if evidence.client_detected else "import"
        base = f"Level 3: AI SDK {kind} exists ({', '.join(evidence.providers)}); no model invocation found."
    else:
        user_in = verification.get("user_input_reaches_model")
        output_used = verification.get("model_output_used")
        base = (
            f"Level 4: model invocation found for {', '.join(evidence.providers)}. "
            f"user_input_reaches_model={user_in}; model_output_used={output_used}."
        )
    if failed:
        return f"{base} Fake-AI findings: {', '.join(failed)}."
    return base or str(llm_providers.get("reasoning") or "")


# Re-export for pipeline / tests
__all__ = ["AiUsageMetric", "scan_manifests"]
