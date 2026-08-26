from __future__ import annotations

import json
import re
from typing import Any

from app.github.client import PATH_HINTS, paths_matching
from app.llm.client import LLMClient
from app.metrics.ai_packages import AI_PACKAGES, is_agent_framework
from app.metrics.base import Metric, MetricContext, MetricResult
from app.pipeline.prompt import build_system_prompt, build_user_prompt

CONFIDENCE_RANK = {"low": 1, "medium": 2, "high": 3}


def _normalize_pkg(name: str) -> str:
    name = name.strip().strip("\"'").lower()
    # strip version pins like openai==1.0.0 or openai>=1
    name = re.split(r"[<=>!~\s\[]", name, maxsplit=1)[0]
    # npm scopes already include @
    return name


def scan_manifests(manifests: dict[str, str]) -> tuple[list[str], list[str]]:
    """Return (all_ai_deps, agent_framework_deps)."""
    found: set[str] = set()
    for path, content in manifests.items():
        lower_path = path.lower()
        if lower_path.endswith("package.json"):
            try:
                data = json.loads(content)
            except json.JSONDecodeError:
                continue
            deps = {
                **(data.get("dependencies") or {}),
                **(data.get("devDependencies") or {}),
                **(data.get("peerDependencies") or {}),
            }
            for pkg in deps:
                key = pkg.lower()
                if key in AI_PACKAGES or key.lstrip("@").split("/")[-1] in {
                    k.lstrip("@").split("/")[-1] for k in AI_PACKAGES
                }:
                    # Match exact or known scoped names
                    if key in AI_PACKAGES:
                        found.add(key)
                    else:
                        for known in AI_PACKAGES:
                            if known == key or known.endswith("/" + key.split("/")[-1]):
                                found.add(known)
                                break
        elif lower_path.endswith(("requirements.txt", "requirements-dev.txt")):
            for line in content.splitlines():
                line = line.strip()
                if not line or line.startswith("#") or line.startswith("-"):
                    continue
                pkg = _normalize_pkg(line)
                if pkg in AI_PACKAGES:
                    found.add(pkg)
        elif lower_path.endswith("pyproject.toml"):
            for m in re.finditer(
                r'["\']([a-zA-Z0-9_.\-]+)["\']\s*[=>~<]|^\s*([a-zA-Z0-9_.\-]+)\s*[=>~<]',
                content,
                re.M,
            ):
                pkg = _normalize_pkg(m.group(1) or m.group(2) or "")
                if pkg in AI_PACKAGES:
                    found.add(pkg)
            # poetry/pep621 dependency tables — also catch bare package names on lines
            for line in content.splitlines():
                m = re.match(r'^\s*([a-zA-Z0-9_.\-]+)\s*=', line)
                if m:
                    pkg = _normalize_pkg(m.group(1))
                    if pkg in AI_PACKAGES:
                        found.add(pkg)
        elif lower_path.endswith("go.mod"):
            for line in content.splitlines():
                parts = line.strip().split()
                if len(parts) >= 1 and any(k in parts[0].lower() for k in ("openai", "langchain")):
                    found.add(parts[0].split("/")[-1].lower())

    ai_list = sorted(found)
    agent_list = [p for p in ai_list if is_agent_framework(p)]
    return ai_list, agent_list


def _import_evidence_paths(tree_paths: list[str], deps: list[str]) -> list[str]:
    """Heuristic: files that likely import the found packages."""
    hints = []
    patterns = [re.compile(re.escape(d.split("/")[-1].replace("-", "[-_]")), re.I) for d in deps]
    for path in tree_paths:
        if not path.endswith((".py", ".ts", ".tsx", ".js", ".jsx", ".mjs")):
            continue
        base = path.lower()
        if any(p.search(base) for p in patterns) or PATH_HINTS.search(path):
            hints.append(path)
    # Prefer agent-ish paths first
    hints.sort(key=lambda p: (0 if PATH_HINTS.search(p) else 1, p))
    return hints[:25]


class AiUsageMetric(Metric):
    name = "ai_usage"
    tier = "static"  # LLM half is gated; catalogue shows static-first
    description = (
        "Static scan of AI dependency manifests; LLM classifies integration type only if deps found."
    )
    default_options = {"min_confidence": "medium", "max_evidence_files": 12}
    skippable_when = "user deselects it; LLM half skipped when no AI deps found"
    output_schema = {
        "type": "object",
        "properties": {
            "ai_dependencies_found": {"type": "array", "items": {"type": "string"}},
            "ai_integration_type": {"type": "string", "enum": ["none", "wrapper", "rag", "agentic"]},
            "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
            "evidence_files": {"type": "array", "items": {"type": "string"}},
            "agent_frameworks_found": {"type": "array", "items": {"type": "string"}},
            "reasoning": {"type": "string"},
        },
    }

    async def run(self, ctx: MetricContext) -> MetricResult:
        opts = {**self.default_options, **ctx.options}
        ai_deps, agent_deps = scan_manifests(ctx.snapshot.package_manifests)

        # Stash for agent_analysis / pipeline gating
        ctx.extras["ai_dependencies_found"] = ai_deps
        ctx.extras["agent_frameworks_found"] = agent_deps

        if not ai_deps:
            return MetricResult(
                name=self.name,
                status="ok",
                data={
                    "ai_dependencies_found": [],
                    "ai_integration_type": "none",
                    "confidence": "high",
                    "evidence_files": [],
                    "agent_frameworks_found": [],
                    "reasoning": "No known AI packages found in dependency manifests.",
                },
            )

        tree_paths = [t["path"] for t in ctx.snapshot.tree]
        evidence_candidates = _import_evidence_paths(tree_paths, ai_deps)
        # Also include paths matching agent hints
        evidence_candidates = list(
            dict.fromkeys(evidence_candidates + paths_matching(ctx.snapshot.tree, PATH_HINTS))
        )[: int(opts.get("max_evidence_files", 12))]

        gh = ctx.extras.get("github_client")
        if gh and evidence_candidates:
            await gh.fetch_files(ctx.snapshot, evidence_candidates, max_file_kb=40)

        evidence_files = [p for p in evidence_candidates if p in ctx.snapshot.file_contents]

        llm: LLMClient | None = ctx.extras.get("llm_client")
        if not llm or not llm.enabled:
            # Static-only fallback classification
            integration = "agentic" if agent_deps else ("rag" if any(
                AI_PACKAGES.get(d) == "vector_db" for d in ai_deps
            ) else "wrapper")
            return MetricResult(
                name=self.name,
                status="ok",
                data={
                    "ai_dependencies_found": ai_deps,
                    "ai_integration_type": integration,
                    "confidence": "low",
                    "evidence_files": evidence_files,
                    "agent_frameworks_found": agent_deps,
                    "reasoning": "Classified from dependency names only (Vertex AI / Gemini not configured).",
                },
            )

        system = build_system_prompt(["ai_usage"])
        user = build_user_prompt(
            metrics=["ai_usage"],
            files={p: ctx.snapshot.file_contents[p] for p in evidence_files},
            hints={"ai_dependencies_found": ai_deps, "agent_frameworks_found": agent_deps},
            submission_context=ctx.extras.get("submission_context"),
        )
        judgment = await llm.judge_json(system=system, user=user)
        section = judgment.get("ai_usage") or judgment

        min_conf = str(opts.get("min_confidence", "medium")).lower()
        conf = str(section.get("confidence", "low")).lower()
        if CONFIDENCE_RANK.get(conf, 0) < CONFIDENCE_RANK.get(min_conf, 0):
            # Soften overconfident claims below threshold by keeping type but noting it
            section["confidence"] = conf

        data: dict[str, Any] = {
            "ai_dependencies_found": ai_deps,
            "ai_integration_type": section.get("ai_integration_type", "wrapper"),
            "confidence": section.get("confidence", "low"),
            "evidence_files": section.get("evidence_files") or evidence_files,
            "agent_frameworks_found": agent_deps,
            "reasoning": section.get("reasoning"),
        }
        return MetricResult(name=self.name, status="ok", data=data)
