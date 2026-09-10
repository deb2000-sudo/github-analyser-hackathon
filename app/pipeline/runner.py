from __future__ import annotations

import asyncio
from typing import Any

from app.analysis.evidence_aggregator import EvidenceAggregator
from app.analysis.facts import build_code_facts
from app.config import get_settings
from app.github.client import GithubClient
from app.github.validation import access_payload
from app.jobs import Job, JobStatus, JobStore
from app.llm.client import LLMClient
from app.llm.reasoner import LLMReasoner, ReasoningRequest
from app.llm.selective import build_evidence_pack, decide_llm_use
from app.metrics.ai_usage import scan_manifests
from app.metrics.base import MetricContext
from app.metrics.registry import get_metric
from app.pipeline.prefetch import collect_prefetch_paths
from app.scoring.aggregator import aggregate_scores, build_gated_result

PIPELINE_ORDER = [
    "repo_health",
    "fullstack",
    "frontend_backend",
    "ai_usage",
    "agent_analysis",
    "solution_fit",
]


def _request_scoring(context: dict[str, Any], options: dict[str, Any]) -> dict[str, Any] | None:
    if options.get("scoring"):
        return options["scoring"]
    if context.get("scoring"):
        return context["scoring"]
    return None


async def run_pipeline(job_id: str) -> None:
    store = JobStore()
    job = await asyncio.to_thread(store.get, job_id)
    if not job:
        return

    await asyncio.to_thread(store.update, job_id, status=JobStatus.running.value)

    settings = get_settings()
    gh = GithubClient(settings=settings)
    llm = LLMClient(settings=settings)

    requested: list[str] = list(job.metrics_requested or [])
    options: dict[str, Any] = dict(job.options or {})
    submission_context: dict[str, Any] = dict(job.context or {})
    request_scoring = _request_scoring(submission_context, options)

    try:
        access = await gh.check_repo_access(job.github_url)
        if not access.is_public:
            result_payload = build_gated_result(
                access,
                github_url=job.github_url,
                submission_context=submission_context,
                request_scoring=request_scoring,
            )
            await asyncio.to_thread(
                store.update,
                job_id,
                result=result_payload,
                status=JobStatus.succeeded.value,
                error=None,
            )
            return

        snapshot = await gh.fetch_snapshot(job.github_url)
        await asyncio.to_thread(store.update, job_id, commit_sha=snapshot.ref.commit_sha)

        ai_deps, agent_deps = scan_manifests(snapshot.package_manifests)

        prefetch_paths = collect_prefetch_paths(
            snapshot,
            requested,
            options,
            ai_deps=ai_deps,
            agent_deps=agent_deps,
        )
        if prefetch_paths:
            await gh.fetch_files(snapshot, prefetch_paths, max_files=100)

        code_facts = build_code_facts(snapshot)
        analysis_payload = code_facts.to_public_dict()

        to_run = list(dict.fromkeys([*requested, "repo_health"]))
        ordered = [m for m in PIPELINE_ORDER if m in to_run]
        llm_candidates = [m for m in ("agent_analysis", "solution_fit") if m in ordered]
        static_first = [m for m in ordered if m not in llm_candidates]
        llm_after = [m for m in ordered if m in llm_candidates]

        results: dict[str, Any] = {}
        ctx = MetricContext(
            snapshot=snapshot,
            prior_results=results,
            extras={
                "github_client": gh,
                "llm_client": llm,
                "submission_context": submission_context,
                "ai_dependencies_found": ai_deps,
                "agent_frameworks_found": agent_deps,
                "llm_judgment": {},
                "skip_file_fetch": True,
                "code_facts": code_facts,
                "code_facts_summary": code_facts.summary(),
            },
        )

        for name in static_first:
            metric = get_metric(name)
            if not metric:
                continue
            if name != "repo_health" and name not in requested:
                continue
            ctx.options = options.get(name) or {}
            result = await metric.run(ctx)
            results[name] = {"status": result.status, **(result.data or {})}
            if result.reason:
                results[name]["skip_reason"] = result.reason
            ctx.prior_results = results

        llm_plan = decide_llm_use(
            requested=requested,
            llm_enabled=llm.enabled,
            static_metrics=results,
            submission_context=submission_context,
            code_facts=code_facts,
            agent_deps=agent_deps,
            confidence_threshold=settings.llm_confidence_threshold,
        )
        ctx.extras["llm_plan"] = llm_plan
        llm_judgment: dict[str, Any] = {}
        if llm_plan.should_run:
            evidence_pack = build_evidence_pack(
                code_facts=code_facts,
                snapshot=snapshot,
                static_metrics=results,
                plan=llm_plan,
            )
            ctx.extras["llm_evidence_pack"] = evidence_pack
            llm_judgment = await combined_llm_judgment(
                llm,
                ReasoningRequest(
                    metrics=llm_plan.metrics,
                    questions=llm_plan.questions,
                    evidence=evidence_pack,
                    submission_context=submission_context,
                    reasons=llm_plan.reasons,
                ),
            )
        ctx.extras["llm_judgment"] = llm_judgment

        for name in llm_after:
            metric = get_metric(name)
            if not metric:
                continue
            ctx.options = options.get(name) or {}
            ctx.prior_results = results
            result = await metric.run(ctx)
            results[name] = {"status": result.status, **(result.data or {})}
            if result.reason:
                results[name]["skip_reason"] = result.reason
            ctx.prior_results = results

        scoring = aggregate_scores(
            results,
            request_scoring=request_scoring,
            access=access,
            snapshot=snapshot,
        )
        verdict = EvidenceAggregator().aggregate(metrics=results, code_facts=code_facts)
        analysis_payload["verdict"] = verdict
        result_payload = {
            "access": access_payload(access),
            "scoring": scoring,
            "verdict": verdict,
            "repo": {
                "owner": snapshot.ref.owner,
                "name": snapshot.ref.name,
                "full_name": snapshot.ref.full_name,
                "default_branch": snapshot.ref.default_branch,
                "commit_sha": snapshot.ref.commit_sha,
            },
            "context": submission_context or None,
            "metrics": results,
            "analysis": analysis_payload,
        }
        await asyncio.to_thread(
            store.update,
            job_id,
            result=result_payload,
            status=JobStatus.succeeded.value,
            error=None,
        )
    except Exception as exc:  # noqa: BLE001
        await asyncio.to_thread(
            store.update,
            job_id,
            status=JobStatus.failed.value,
            error=str(exc),
        )


async def combined_llm_judgment(
    reasoner: LLMReasoner,
    request: ReasoningRequest,
) -> dict[str, Any]:
    """Single selective Gemini call over a curated evidence pack."""
    return await reasoner.reason(request)
