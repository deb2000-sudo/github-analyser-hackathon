from __future__ import annotations

import re
from typing import Any

from app.analysis.context import get_analysis
from app.analysis.solution_evidence import (
    build_solution_evidence_pack,
    normalize_solution_judgment,
)
from app.llm.reasoner import LLMReasoner, ReasoningRequest
from app.llm.selective import LlmPlan, decide_llm_use
from app.metrics.base import Metric, MetricContext, MetricResult
from app.metrics.solution_fit_normalize import normalize_solution_fit
from app.scoring.readme_quality import analyze_readme, find_readme_content

README_RE = re.compile(r"(^|/)readme(\.[a-z0-9]+)?$", re.I)
ENTRY_HINTS = re.compile(
    r"(main\.py|app\.py|index\.(tsx?|jsx?|html)|App\.(tsx?|jsx?)|server\.|router\.|agent)",
    re.I,
)


def _curate_paths(tree: list[dict[str, Any]], max_files: int = 18) -> list[str]:
    paths = [t["path"] for t in tree if t.get("type") == "blob"]
    paths = [p for p in paths if "node_modules" not in p and ".venv" not in p and "/dist/" not in p]

    readmes = [p for p in paths if README_RE.search(p.split("/")[-1])]
    entries = [p for p in paths if ENTRY_HINTS.search(p) and p.endswith((".py", ".ts", ".tsx", ".js", ".jsx", ".md"))]
    others = [
        p
        for p in paths
        if p not in readmes
        and p not in entries
        and p.endswith((".py", ".ts", ".tsx", ".js", ".jsx", ".md", ".toml", ".yml", ".yaml"))
    ]
    ordered = list(dict.fromkeys(readmes + entries + others))
    return ordered[:max_files]


def _readme_payload(snapshot: Any, llm_section: dict[str, Any] | None = None) -> dict[str, Any]:
    _, content = find_readme_content(snapshot.tree, snapshot.file_contents)
    static = analyze_readme(content)
    if not llm_section:
        return static
    llm_score = llm_section.get("readme_quality_score")
    try:
        llm_score_f = float(llm_score) if llm_score is not None else None
    except (TypeError, ValueError):
        llm_score_f = None
    if llm_score_f is not None:
        static["readme_quality_score"] = round(max(static["readme_quality_score"], llm_score_f), 1)
    if llm_section.get("readme_has_local_setup") is True:
        static["readme_has_local_setup"] = True
    llm_note = (llm_section.get("readme_reasoning") or "").strip()
    if llm_note:
        static["reasoning"] = f"{static['reasoning']} LLM: {llm_note}"
    return static


class SolutionFitMetric(Metric):
    name = "solution_fit"
    tier = "llm"
    description = (
        "Compares context.provided_context to deterministic implementation evidence "
        "(architecture, routes, AI/agent results, selected snippets). Never sends the full repo."
    )
    depends_on = []
    requires_context = True
    default_options = {"max_files": 12, "max_file_kb": 40}
    skippable_when = "no evaluation context provided, or LLM unavailable"
    output_schema = {
        "type": "object",
        "properties": {
            "context_relevant": {"type": "boolean"},
            "relevance_score": {"type": "number"},
            "alignment_score": {"type": "number"},
            "implements_claimed_solution": {"type": "boolean"},
            "context_requirements_met": {"type": "array"},
            "implementation_matches_claim": {"type": "boolean"},
            "verified_features": {"type": "array"},
            "unsupported_features": {"type": "array"},
            "partial_features": {"type": "array"},
            "evidence_ids": {"type": "array", "items": {"type": "string"}},
            "gaps": {"type": "array", "items": {"type": "string"}},
            "strengths": {"type": "array", "items": {"type": "string"}},
            "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
            "reasoning": {"type": "string"},
        },
    }

    def _build_data(self, section: dict[str, Any], snapshot: Any, pack: dict[str, Any] | None = None) -> dict[str, Any]:
        section = normalize_solution_judgment(section, pack or {"evidence": []})
        normalized = normalize_solution_fit(section)
        reqs = normalized.get("context_requirements_met") or normalized.get("requirements_met") or []
        return {
            "context_relevant": bool(normalized.get("context_relevant")),
            "relevance_score": normalized.get("relevance_score"),
            "alignment_score": normalized.get("alignment_score"),
            "implements_claimed_solution": bool(normalized.get("implements_claimed_solution")),
            "implementation_matches_claim": bool(
                normalized.get("implementation_matches_claim", normalized.get("implements_claimed_solution"))
            ),
            "verified_features": normalized.get("verified_features") or [],
            "unsupported_features": normalized.get("unsupported_features") or [],
            "partial_features": normalized.get("partial_features") or [],
            "evidence_ids": normalized.get("evidence_ids") or [],
            "context_requirements_met": reqs,
            "requirements_met": reqs,  # legacy alias for frontend transition
            "gaps": normalized.get("gaps") or [],
            "strengths": normalized.get("strengths") or [],
            "confidence": normalized.get("confidence", "low"),
            "reasoning": normalized.get("reasoning"),
            "readme": _readme_payload(snapshot, normalized),
            "status": "ok",
        }

    async def run(self, ctx: MetricContext) -> MetricResult:
        opts = {**self.default_options, **ctx.options}
        submission = ctx.extras.get("submission_context") or {}
        eval_context = (submission.get("provided_context") or "").strip()

        if not eval_context:
            return MetricResult(
                name=self.name,
                status="skipped",
                data={
                    "status": "skipped",
                    "context_relevant": False,
                    "relevance_score": 0.0,
                    "alignment_score": 0.0,
                    "implements_claimed_solution": False,
                    "implementation_matches_claim": False,
                    "verified_features": [],
                    "unsupported_features": [],
                    "partial_features": [],
                    "evidence_ids": [],
                    "context_requirements_met": [],
                    "requirements_met": [],
                    "gaps": ["No project context provided — cannot evaluate solution fit."],
                    "strengths": [],
                    "confidence": "high",
                    "reasoning": "solution_fit requires context.provided_context.",
                    "readme": _readme_payload(ctx.snapshot),
                },
                reason="missing_submission_context",
            )

        llm: LLMReasoner | None = ctx.extras.get("llm_client")
        if not llm or not llm.enabled:
            return MetricResult(
                name=self.name,
                status="skipped",
                data={
                    "status": "skipped",
                    "alignment_score": None,
                    "implements_claimed_solution": False,
                    "requirements_met": [],
                    "gaps": [],
                    "strengths": [],
                    "confidence": "low",
                    "reasoning": "Vertex AI not configured; cannot run solution_fit.",
                },
                reason="llm_not_configured",
            )

        analysis = get_analysis(ctx)
        # Prefetch happens once in the pipeline. This metric does not fetch the repo.
        pack = build_solution_evidence_pack(
            code_facts=analysis.code_facts,
            snapshot=ctx.snapshot,
            static_metrics=ctx.prior_results,
            submission_context=submission,
            max_files=int(opts.get("max_files", 12)),
        )
        pack = {**pack, "prior_metric_summaries": _prior_summaries(ctx.prior_results)}

        precomputed = (ctx.extras.get("llm_judgment") or {}).get("solution_fit")
        if precomputed:
            return MetricResult(
                name=self.name,
                status="ok",
                data=self._build_data(precomputed, ctx.snapshot, pack),
            )

        plan: LlmPlan | None = ctx.extras.get("llm_plan")
        if plan is None:
            plan = decide_llm_use(
                requested=["solution_fit"],
                llm_enabled=True,
                static_metrics=ctx.prior_results,
                submission_context=submission,
                code_facts=analysis.code_facts,
            )

        judgment = await llm.reason(
            ReasoningRequest(
                metrics=["solution_fit"],
                questions=plan.questions
                or [
                    "Does the implementation match the claimed project in PROJECT CONTEXT?",
                    "Which claimed features are verified, unsupported, or only partially implemented?",
                ],
                evidence=pack,
                submission_context=submission,
                reasons=plan.reasons,
            )
        )
        section = judgment.get("solution_fit") or judgment
        return MetricResult(name=self.name, status="ok", data=self._build_data(section, ctx.snapshot, pack))


def _prior_summaries(prior: dict[str, Any]) -> dict[str, Any]:
    """Compact hints from earlier static metrics for the LLM."""
    out: dict[str, Any] = {}
    if "fullstack" in prior:
        fs = prior["fullstack"]
        out["fullstack"] = {
            "application_type": fs.get("application_type"),
            "frontend": fs.get("frontend"),
            "backend": fs.get("backend"),
        }
    if "ai_usage" in prior:
        out["ai_usage"] = {
            "ai_integration_type": prior["ai_usage"].get("ai_integration_type"),
            "ai_dependencies_found": prior["ai_usage"].get("ai_dependencies_found"),
        }
    if "repo_health" in prior:
        out["repo_health"] = {
            "commit_count": prior["repo_health"].get("commit_count"),
            "flag_single_dump": prior["repo_health"].get("flag_single_dump"),
            "contributors": prior["repo_health"].get("contributors"),
        }
    return out
