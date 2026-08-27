from __future__ import annotations

from typing import Any

from app.github.client import PATH_HINTS, RepoSnapshot, paths_matching
from app.metrics.ai_usage import scan_manifests
from app.metrics.fullstack import select_core_paths
from app.metrics.solution_fit import _curate_paths

MANIFEST_NAMES = frozenset(
    {
        "package.json",
        "requirements.txt",
        "pyproject.toml",
        "go.mod",
        "pom.xml",
    }
)


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
    """Build the file bundle for a single combined Gemini call."""
    paths = collect_prefetch_paths(
        snapshot,
        llm_metrics,
        options,
        ai_deps=ai_deps,
        agent_deps=agent_deps,
    )
    files = {p: snapshot.file_contents[p] for p in paths if p in snapshot.file_contents}
    if "solution_fit" in llm_metrics:
        tree_summary = "\n".join(t["path"] for t in snapshot.tree[:150])
        files = {"__repo_tree__.txt": tree_summary, **files}
    return files


def resolve_llm_metrics(
    requested: list[str],
    *,
    ai_deps: list[str],
    agent_deps: list[str],
    has_evaluation_context: bool,
    llm_enabled: bool,
) -> list[str]:
    del ai_deps, agent_deps  # agent_analysis always runs when requested
    if not llm_enabled:
        return []
    out: list[str] = []
    if "ai_usage" in requested:
        out.append("ai_usage")
    if "agent_analysis" in requested:
        out.append("agent_analysis")
    if "solution_fit" in requested and has_evaluation_context:
        out.append("solution_fit")
    return out
