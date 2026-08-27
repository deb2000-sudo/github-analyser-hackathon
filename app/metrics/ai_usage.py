from __future__ import annotations

from typing import Any

from app.github.client import PATH_HINTS, paths_matching
from app.llm.client import LLMClient
from app.metrics.ai_imports import (
    reconcile_ai_dependencies,
    scan_code_for_ai_imports,
    scan_generic_ai_usage,
    scan_manifests,
    select_ai_evidence_paths,
)
from app.metrics.ai_packages import AI_PACKAGES
from app.metrics.base import Metric, MetricContext, MetricResult
from app.scoring.llm_providers import detect_llm_providers
from app.pipeline.prompt import build_system_prompt, build_user_prompt

CONFIDENCE_RANK = {"low": 1, "medium": 2, "high": 3}


class AiUsageMetric(Metric):
    name = "ai_usage"
    tier = "static"
    description = (
        "Verifies AI/agent dependencies from manifests and source imports; "
        "LLM classifies integration type from evidenced files."
    )
    default_options = {"min_confidence": "medium", "max_evidence_files": 12}
    skippable_when = "no verified AI packages or AI code patterns in scanned files"
    output_schema = {
        "type": "object",
        "properties": {
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
        opts = {**self.default_options, **ctx.options}
        raw_manifest_deps, _ = scan_manifests(ctx.snapshot.package_manifests)
        tree_paths = [t["path"] for t in ctx.snapshot.tree]

        prelim_paths = select_ai_evidence_paths(
            tree_paths,
            ctx.snapshot.file_contents,
            raw_manifest_deps,
            {},
            max_files=int(opts.get("max_evidence_files", 12)) + 8,
        )
        gh = ctx.extras.get("github_client")
        if gh and prelim_paths and not ctx.extras.get("skip_file_fetch"):
            await gh.fetch_files(ctx.snapshot, prelim_paths, max_file_kb=40)

        code_hits = scan_code_for_ai_imports(ctx.snapshot.file_contents)
        reconciled = reconcile_ai_dependencies(raw_manifest_deps, code_hits)
        verified_deps = reconciled["ai_dependencies_found"]
        agent_deps = reconciled["agent_frameworks_found"]
        generic_files = scan_generic_ai_usage(ctx.snapshot.file_contents)

        ctx.extras["ai_dependencies_found"] = verified_deps
        ctx.extras["agent_frameworks_found"] = agent_deps

        evidence_candidates = select_ai_evidence_paths(
            tree_paths,
            ctx.snapshot.file_contents,
            verified_deps,
            reconciled["code_evidence_by_package"],
            max_files=int(opts.get("max_evidence_files", 12)),
        )
        if not evidence_candidates and generic_files:
            evidence_candidates = generic_files[: int(opts.get("max_evidence_files", 12))]

        if gh and evidence_candidates and not ctx.extras.get("skip_file_fetch"):
            missing = [p for p in evidence_candidates if p not in ctx.snapshot.file_contents]
            if missing:
                await gh.fetch_files(ctx.snapshot, missing, max_file_kb=40)

        evidence_files = [p for p in evidence_candidates if p in ctx.snapshot.file_contents]

        llm_providers = detect_llm_providers(
            verified_deps, ctx.snapshot.file_contents, evidence_files
        )

        diagnostics = {
            "manifest_deps_raw": reconciled["manifest_deps_raw"],
            "manifest_only_deps": reconciled["manifest_only_deps"],
            "rejected_false_manifest_deps": reconciled["rejected_false_manifest_deps"],
            "code_evidence_by_package": reconciled["code_evidence_by_package"],
        }

        if not verified_deps and not generic_files:
            rejected = reconciled["rejected_false_manifest_deps"]
            note = ""
            if rejected:
                note = (
                    f" Manifest listed {', '.join(rejected)} but no matching imports in source — ignored."
                )
            return MetricResult(
                name=self.name,
                status="ok",
                data={
                    "ai_dependencies_found": [],
                    "ai_integration_type": "none",
                    "confidence": "high",
                    "evidence_files": [],
                    "agent_frameworks_found": [],
                    "llm_providers": llm_providers,
                    "reasoning": f"No verified AI SDK imports or AI API usage in scanned files.{note}",
                    **diagnostics,
                },
            )

        hints = {
            "verified_ai_dependencies": verified_deps,
            "agent_frameworks_found": agent_deps,
            "manifest_only_deps": reconciled["manifest_only_deps"],
            "rejected_false_manifest_deps": reconciled["rejected_false_manifest_deps"],
            "code_evidence_by_package": reconciled["code_evidence_by_package"],
            "generic_ai_files": generic_files[:8],
        }

        precomputed = (ctx.extras.get("llm_judgment") or {}).get("ai_usage")
        if precomputed:
            section = precomputed
            conf = str(section.get("confidence", "low")).lower()
            return MetricResult(
                name=self.name,
                status="ok",
                data=self._build_data(
                    section,
                    verified_deps,
                    agent_deps,
                    evidence_files,
                    llm_providers,
                    diagnostics,
                    conf,
                ),
            )

        llm: LLMClient | None = ctx.extras.get("llm_client")
        if not llm or not llm.enabled:
            integration = self._static_integration(verified_deps, agent_deps, generic_files)
            return MetricResult(
                name=self.name,
                status="ok",
                data={
                    "ai_dependencies_found": verified_deps,
                    "ai_integration_type": integration,
                    "confidence": "low",
                    "evidence_files": evidence_files,
                    "agent_frameworks_found": agent_deps,
                    "llm_providers": llm_providers,
                    "reasoning": self._static_reasoning(
                        verified_deps, agent_deps, reconciled, generic_files
                    ),
                    **diagnostics,
                },
            )

        system = build_system_prompt(["ai_usage"])
        user = build_user_prompt(
            metrics=["ai_usage"],
            files={p: ctx.snapshot.file_contents[p] for p in evidence_files},
            hints=hints,
            submission_context=ctx.extras.get("submission_context"),
        )
        judgment = await llm.judge_json(system=system, user=user)
        section = judgment.get("ai_usage") or judgment
        conf = str(section.get("confidence", "low")).lower()
        return MetricResult(
            name=self.name,
            status="ok",
            data=self._build_data(
                section,
                verified_deps,
                agent_deps,
                evidence_files,
                llm_providers,
                diagnostics,
                conf,
            ),
        )

    @staticmethod
    def _static_integration(
        verified_deps: list[str],
        agent_deps: list[str],
        generic_files: list[str],
    ) -> str:
        if agent_deps:
            return "agentic"
        if any(AI_PACKAGES.get(d) == "vector_db" for d in verified_deps):
            return "rag"
        if verified_deps or generic_files:
            return "wrapper"
        return "none"

    @staticmethod
    def _static_reasoning(
        verified_deps: list[str],
        agent_deps: list[str],
        reconciled: dict[str, Any],
        generic_files: list[str],
    ) -> str:
        parts = []
        if verified_deps:
            parts.append(f"Verified deps: {', '.join(verified_deps)}")
        if agent_deps:
            parts.append(f"Agent frameworks with code evidence: {', '.join(agent_deps)}")
        rejected = reconciled.get("rejected_false_manifest_deps") or []
        if rejected:
            parts.append(
                f"Ignored manifest-only agent packages without imports: {', '.join(rejected)}"
            )
        if generic_files:
            parts.append(f"AI API patterns in: {', '.join(generic_files[:3])}")
        return ". ".join(parts) or "No AI usage detected."

    @staticmethod
    def _build_data(
        section: dict[str, Any],
        verified_deps: list[str],
        agent_deps: list[str],
        evidence_files: list[str],
        llm_providers: dict[str, Any],
        diagnostics: dict[str, Any],
        conf: str,
    ) -> dict[str, Any]:
        return {
            "ai_dependencies_found": verified_deps,
            "ai_integration_type": section.get("ai_integration_type", "wrapper"),
            "confidence": conf,
            "evidence_files": section.get("evidence_files") or evidence_files,
            "agent_frameworks_found": agent_deps,
            "llm_providers": llm_providers,
            "reasoning": section.get("reasoning"),
            **diagnostics,
        }


# Re-export for pipeline / tests
__all__ = ["AiUsageMetric", "scan_manifests"]
