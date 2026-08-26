from __future__ import annotations

import re
from typing import Any

from app.llm.client import LLMClient
from app.metrics.base import Metric, MetricContext, MetricResult
from app.pipeline.prompt import build_system_prompt, build_user_prompt

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


class SolutionFitMetric(Metric):
    name = "solution_fit"
    tier = "llm"
    description = (
        "Evaluates how well the repo addresses the hackathon problem statement "
        "and the team's claimed solution (requires submission context)."
    )
    depends_on = []
    requires_context = True
    default_options = {"max_files": 18, "max_file_kb": 40}
    skippable_when = "no problem_statement / submission context provided, or LLM unavailable"
    output_schema = {
        "type": "object",
        "properties": {
            "alignment_score": {"type": "number"},
            "implements_claimed_solution": {"type": "boolean"},
            "requirements_met": {"type": "array"},
            "gaps": {"type": "array", "items": {"type": "string"}},
            "strengths": {"type": "array", "items": {"type": "string"}},
            "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
            "reasoning": {"type": "string"},
        },
    }

    async def run(self, ctx: MetricContext) -> MetricResult:
        opts = {**self.default_options, **ctx.options}
        submission = ctx.extras.get("submission_context") or {}
        problem = (submission.get("problem_statement") or "").strip()

        if not problem:
            return MetricResult(
                name=self.name,
                status="skipped",
                data={
                    "status": "skipped",
                    "alignment_score": None,
                    "implements_claimed_solution": False,
                    "requirements_met": [],
                    "gaps": ["No problem_statement provided in submission context."],
                    "strengths": [],
                    "confidence": "high",
                    "reasoning": "solution_fit requires hackathon problem/solution context.",
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

        candidates = _curate_paths(ctx.snapshot.tree, max_files=int(opts.get("max_files", 18)))
        gh = ctx.extras.get("github_client")
        if gh and candidates:
            await gh.fetch_files(
                ctx.snapshot, candidates, max_file_kb=int(opts.get("max_file_kb", 40))
            )

        files = {p: ctx.snapshot.file_contents[p] for p in candidates if p in ctx.snapshot.file_contents}
        # Always include a tree summary so Gemini sees structure even if fetch fails
        tree_summary = "\n".join(t["path"] for t in ctx.snapshot.tree[:200])
        files = {"__repo_tree__.txt": tree_summary, **files}

        system = build_system_prompt(["solution_fit"])
        user = build_user_prompt(
            metrics=["solution_fit"],
            files=files,
            hints={"prior_metric_summaries": _prior_summaries(ctx.prior_results)},
            submission_context=submission,
        )
        judgment = await llm.judge_json(system=system, user=user)
        section = judgment.get("solution_fit") or judgment

        score = section.get("alignment_score")
        try:
            score_f = float(score) if score is not None else None
            if score_f is not None:
                score_f = max(0.0, min(10.0, score_f))
        except (TypeError, ValueError):
            score_f = None

        data: dict[str, Any] = {
            "alignment_score": score_f,
            "implements_claimed_solution": bool(section.get("implements_claimed_solution", False)),
            "requirements_met": section.get("requirements_met") or [],
            "gaps": section.get("gaps") or [],
            "strengths": section.get("strengths") or [],
            "confidence": section.get("confidence", "low"),
            "reasoning": section.get("reasoning"),
            "status": "ok",
        }
        return MetricResult(name=self.name, status="ok", data=data)


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
