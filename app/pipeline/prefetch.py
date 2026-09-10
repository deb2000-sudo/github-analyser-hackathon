from __future__ import annotations

from typing import Any

from app.analysis.extract import select_parseable_paths
from app.github.client import PATH_HINTS, RepoSnapshot, paths_matching
from app.metrics.ai_usage import scan_manifests
from app.metrics.fullstack import select_core_paths
from app.metrics.solution_fit import _curate_paths


def collect_prefetch_paths(
    snapshot: RepoSnapshot,
    requested: list[str],
    options: dict[str, Any],
    *,
    ai_deps: list[str] | None = None,
    agent_deps: list[str] | None = None,
) -> list[str]:
    """Union of file paths all requested metrics may read — fetch once in parallel."""
    paths: list[str] = []
    tree_paths = [t["path"] for t in snapshot.tree]
    paths.extend(select_parseable_paths(tree_paths))

    if "fullstack" in requested:
        paths.extend(select_core_paths(tree_paths))

    ai_opts = options.get("ai_usage") or {}
    agent_opts = options.get("agent_analysis") or {}
    fit_opts = options.get("solution_fit") or {}

    if ai_deps is None or agent_deps is None:
        ai_deps, agent_deps = scan_manifests(snapshot.package_manifests)

    if "ai_usage" in requested:
        max_evidence = int(ai_opts.get("max_evidence_files", 12))
        from app.metrics.ai_imports import select_ai_evidence_paths, scan_manifests

        raw_deps, _ = scan_manifests(snapshot.package_manifests)
        evidence = select_ai_evidence_paths(
            tree_paths,
            snapshot.file_contents,
            raw_deps,
            {},
            max_files=max_evidence,
        )
        paths.extend(evidence)

    if "agent_analysis" in requested:
        max_files = int(agent_opts.get("max_files", 10))
        candidates = [
            p
            for p in paths_matching(snapshot.tree, PATH_HINTS)
            if p.endswith((".py", ".ts", ".tsx", ".js", ".jsx", ".mjs"))
            and "node_modules" not in p
            and ".venv" not in p
        ][:max_files]
        if not candidates:
            candidates = _curate_paths(snapshot.tree, max_files=max_files)
        paths.extend(candidates)

    if "solution_fit" in requested:
        max_files = int(fit_opts.get("max_files", 12))
        paths.extend(_curate_paths(snapshot.tree, max_files=max_files))

    out: list[str] = []
    seen: set[str] = set()
    for p in paths:
        if p not in seen and p != "__repo_tree__.txt":
            seen.add(p)
            out.append(p)
    return out


def collect_llm_files(
    snapshot: RepoSnapshot,
    llm_metrics: list[str],
    options: dict[str, Any],
    *,
    ai_deps: list[str],
    agent_deps: list[str],
) -> dict[str, str]:
    """Small evidence-file snippets only. Never the entire repository."""
    del llm_metrics
    paths = collect_prefetch_paths(
        snapshot,
        ["ai_usage", "agent_analysis"],
        options,
        ai_deps=ai_deps,
        agent_deps=agent_deps,
    )
    files: dict[str, str] = {}
    for path in paths:
        content = snapshot.file_contents.get(path)
        if not content:
            continue
        files[path] = content if len(content) <= 4000 else content[:4000] + "\n…[truncated]…"
        if len(files) >= 8:
            break
    return files


def resolve_llm_metrics(
    requested: list[str],
    *,
    ai_deps: list[str],
    agent_deps: list[str],
    has_evaluation_context: bool,
    llm_enabled: bool,
    static_metrics: dict[str, Any] | None = None,
    code_facts: Any | None = None,
    submission_context: dict[str, Any] | None = None,
    confidence_threshold: float | None = None,
) -> list[str]:
    """Sections Gemini may answer. Empty when no semantic work is required."""
    from app.llm.selective import DEFAULT_CONFIDENCE_THRESHOLD, decide_llm_use

    context = dict(submission_context or {})
    if has_evaluation_context and not (context.get("provided_context") or "").strip():
        context["provided_context"] = "provided"
    plan = decide_llm_use(
        requested=requested,
        llm_enabled=llm_enabled,
        static_metrics=static_metrics,
        submission_context=context,
        code_facts=code_facts,
        agent_deps=agent_deps,
        confidence_threshold=(
            DEFAULT_CONFIDENCE_THRESHOLD if confidence_threshold is None else confidence_threshold
        ),
    )
    del ai_deps
    return plan.metrics
