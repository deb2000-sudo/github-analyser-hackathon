from __future__ import annotations

import re
from typing import Any

from app.llm.client import LLMClient
from app.metrics.base import Metric, MetricContext, MetricResult
from app.pipeline.prompt import build_system_prompt, build_user_prompt
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
        "Evaluates how well the repo matches the project context paragraph "
        "(requires context.provided_context)."
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
            "gaps": {"type": "array", "items": {"type": "string"}},
            "strengths": {"type": "array", "items": {"type": "string"}},
            "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
            "reasoning": {"type": "string"},
        },
    }

    def _build_data(self, section: dict[str, Any], snapshot: Any) -> dict[str, Any]:
        normalized = normalize_solution_fit(section)
        reqs = normalized.get("context_requirements_met") or normalized.get("requirements_met") or []
        return {
            "context_relevant": bool(normalized.get("context_relevant")),
            "relevance_score": normalized.get("relevance_score"),
            "alignment_score": normalized.get("alignment_score"),
            "implements_claimed_solution": bool(normalized.get("implements_claimed_solution")),
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

        llm: LLMClient | None = ctx.extras.get("llm_client")
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

        candidates = _curate_paths(ctx.snapshot.tree, max_files=int(opts.get("max_files", 12)))
        gh = ctx.extras.get("github_client")
        if gh and candidates and not ctx.extras.get("skip_file_fetch"):
            await gh.fetch_files(
                ctx.snapshot, candidates, max_file_kb=int(opts.get("max_file_kb", 40))
            )

        files = {p: ctx.snapshot.file_contents[p] for p in candidates if p in ctx.snapshot.file_contents}
        tree_summary = "\n".join(t["path"] for t in ctx.snapshot.tree[:150])
        files = {"__repo_tree__.txt": tree_summary, **files}

        precomputed = (ctx.extras.get("llm_judgment") or {}).get("solution_fit")
        if precomputed:
            return MetricResult(
                name=self.name,
                status="ok",
                data=self._build_data(precomputed, ctx.snapshot),
            )

        system = build_system_prompt(["solution_fit"])
        user = build_user_prompt(
            metrics=["solution_fit"],
            files=files,
            hints={"prior_metric_summaries": _prior_summaries(ctx.prior_results)},
            submission_context=submission,
        )
        judgment = await llm.judge_json(system=system, user=user)
        section = judgment.get("solution_fit") or judgment
        return MetricResult(name=self.name, status="ok", data=self._build_data(section, ctx.snapshot))


def _prior_summaries(prior: dict[str, Any]) -> dict[str, Any]:
    """Compact hints from earlier static metrics for the LLM."""
    out: dict[str, Any] = {}
    if "fullstack" in prior:
        out["fullstack"] = {
            "is_fullstack": prior["fullstack"].get("is_fullstack"),
            "frontend": prior["fullstack"].get("frontend_detected"),
            "backend": prior["fullstack"].get("backend_detected"),
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
