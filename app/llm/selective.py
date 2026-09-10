"""Decide whether Gemini should run and build a curated evidence pack.

Deterministic facts (packages, routes, imports, SDK calls) are never asked
of the LLM. The pack never includes the full repository.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.analysis.ai_fake import (
    RULE_DISCARDED,
    RULE_FALLBACK,
    RULE_HARDCODED,
    RULE_POOL,
    RULE_UNUSED_DEP,
)

DEFAULT_CONFIDENCE_THRESHOLD = 0.75
MAX_SNIPPETS = 8
SNIPPET_RADIUS = 12
MAX_SNIPPET_CHARS = 1400
MAX_RELEVANT_FACTS = 80
MAX_PATHS = 16

_AGENT_HINT = re.compile(
    r"(agent|orchestrat|supervisor|langgraph|crewai|autogen|handoff|tool_router|workflow)",
    re.I,
)
_AI_HINT = re.compile(
    r"(openai|anthropic|gemini|groq|mistral|huggingface|ollama|langchain|llamaindex|"
    r"generate_content|chat\.completions|responses\.create)",
    re.I,
)

_LABEL_CONFIDENCE = {"low": 0.4, "medium": 0.7, "high": 0.9}

DETERMINISTIC_QUESTIONS = (
    "Is FastAPI installed?",
    "Does /api/chat route exist?",
    "Is OpenAI SDK imported?",
    "Is generate_content() called?",
)


@dataclass
class LlmPlan:
    """Whether a selective Gemini call should run, and why."""

    metrics: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    uncertainties: list[str] = field(default_factory=list)

    @property
    def should_run(self) -> bool:
        return bool(self.metrics)


def decide_llm_use(
    *,
    requested: list[str],
    llm_enabled: bool,
    static_metrics: dict[str, Any] | None = None,
    submission_context: dict[str, Any] | None = None,
    code_facts: Any | None = None,
    agent_deps: list[str] | None = None,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
) -> LlmPlan:
    """Return which semantic sections Gemini may answer. Empty if none."""
    plan = LlmPlan()
    if not llm_enabled:
        return plan

    metrics = static_metrics or {}
    ai = metrics.get("ai_usage") or {}
    agent_pkgs = list(agent_deps or ai.get("agent_frameworks_found") or [])
    has_context = bool(((submission_context or {}).get("provided_context") or "").strip())
    conflicts, uncertainties = _conflicts_and_uncertainties(ai, confidence_threshold)
    plan.conflicts = conflicts
    plan.uncertainties = uncertainties
    low_confidence = bool(uncertainties)
    has_conflict = bool(conflicts)
    invocation = bool(ai.get("model_invocation_detected"))
    agent_like = _has_agent_like_symbols(code_facts)

    if "solution_fit" in requested and has_context:
        plan.metrics.append("solution_fit")
        plan.questions.append(
            "Does repository implementation meaningfully fit the provided hackathon problem statement?"
        )
        plan.reasons.append("solution_fit_requires_semantic_interpretation")

    semantic_agents = bool(
        "agent_analysis" in requested
        and (agent_pkgs or agent_like or invocation or has_conflict or low_confidence)
    )
    if semantic_agents:
        plan.metrics.append("agent_analysis")
        plan.questions.extend(
            [
                "Is this meaningful agent orchestration?",
                "Is this just a simple LLM wrapper?",
                "Are the extracted components semantically being used as agents/tools/workflows?",
            ]
        )
        if agent_pkgs or agent_like:
            plan.reasons.append("agent_analysis_requires_semantic_interpretation")
        elif invocation:
            plan.reasons.append("wrapper_vs_orchestration_requires_interpretation")

    if has_conflict:
        plan.questions.append("Resolve conflicting findings: " + "; ".join(conflicts))
        if "evidence_conflicts" not in plan.reasons:
            plan.reasons.append("evidence_conflicts")
        if "agent_analysis" in requested and "agent_analysis" not in plan.metrics:
            plan.metrics.append("agent_analysis")
            plan.questions.extend(
                [
                    "Is this meaningful agent orchestration?",
                    "Is this just a simple LLM wrapper?",
                ]
            )

    if low_confidence:
        if "confidence_below_threshold" not in plan.reasons:
            plan.reasons.append("confidence_below_threshold")
        if "agent_analysis" in requested and "agent_analysis" not in plan.metrics:
            plan.metrics.append("agent_analysis")
            plan.questions.append("Is this just a simple LLM wrapper?")

    plan.metrics = list(dict.fromkeys(plan.metrics))
    plan.questions = list(dict.fromkeys(plan.questions))
    return plan


def build_evidence_pack(
    *,
    code_facts: Any | None,
    snapshot: Any | None,
    static_metrics: dict[str, Any] | None,
    plan: LlmPlan,
    max_snippets: int = MAX_SNIPPETS,
) -> dict[str, Any]:
    """Curated pack: facts, snippets, paths, findings, uncertainties. Not the repo."""
    metrics = static_metrics or {}
    ai = metrics.get("ai_usage") or {}
    files = getattr(snapshot, "file_contents", None) or {}
    facts = list(getattr(code_facts, "source_facts", None) or [])
    call_graph = getattr(code_facts, "call_graph", None)
    data_flow = getattr(code_facts, "data_flow", None)

    pack = {
        "code_facts": _relevant_code_facts(code_facts, facts),
        "snippets": _select_snippets(files, ai, facts, max_snippets=max_snippets),
        "call_paths": _call_paths(ai, call_graph),
        "data_flow_paths": _data_flow_paths(ai, data_flow),
        "deterministic_findings": _deterministic_findings(metrics),
        "uncertainties": list(plan.uncertainties),
        "conflicts": list(plan.conflicts),
        "invoke_reasons": list(plan.reasons),
    }
    return pack


def _conflicts_and_uncertainties(
    ai: dict[str, Any],
    threshold: float,
) -> tuple[list[str], list[str]]:
    conflicts: list[str] = []
    uncertainties: list[str] = []
    findings = list(ai.get("ai_findings") or [])
    failed = {str(item.get("rule_id")) for item in findings if item.get("status") == "failed"}
    invocation = bool(ai.get("model_invocation_detected"))
    output_used = bool(ai.get("model_output_used"))
    input_reaches = bool(ai.get("user_input_reaches_model"))

    if invocation and RULE_DISCARDED in failed:
        conflicts.append("model invocation exists but AI output is discarded")
    if invocation and RULE_HARDCODED in failed:
        conflicts.append("model invocation exists alongside a hardcoded final response")
    if invocation and RULE_POOL in failed:
        conflicts.append("model invocation exists alongside a static response pool")
    if invocation and RULE_FALLBACK in failed:
        conflicts.append("model invocation exists but a fake fallback appears to dominate")
    if invocation and RULE_UNUSED_DEP in failed:
        conflicts.append("unused-dependency finding contradicts a detected invocation")
    if input_reaches and invocation and not output_used:
        uncertainties.append("user input reaches the model but output use is unproven")
    if str(ai.get("ai_integration_type") or "") == "agentic" and not (
        ai.get("agent_frameworks_found")
    ):
        conflicts.append("integration type is agentic but no agent framework import was proven")

    numeric = _numeric_confidence(ai)
    if numeric is not None and numeric < threshold:
        uncertainties.append(
            f"static AI confidence {numeric:.2f} is below threshold {threshold:.2f}"
        )
    return conflicts, uncertainties


def _numeric_confidence(ai: dict[str, Any]) -> float | None:
    verification = ai.get("ai_verification") or {}
    raw = verification.get("confidence")
    if isinstance(raw, (int, float)):
        return float(raw)
    label = str(ai.get("confidence") or "").lower()
    if label in _LABEL_CONFIDENCE:
        return _LABEL_CONFIDENCE[label]
    return None


def _has_agent_like_symbols(code_facts: Any | None) -> bool:
    facts = list(getattr(code_facts, "source_facts", None) or [])
    for item in facts:
        blob = " ".join(
            str(item.get(key) or "")
            for key in ("name", "callee", "file", "handler", "role")
        )
        if _AGENT_HINT.search(blob):
            return True
    return False


def _relevant_code_facts(code_facts: Any | None, facts: list[dict[str, Any]]) -> dict[str, Any]:
    summary = code_facts.summary() if code_facts is not None and hasattr(code_facts, "summary") else {}
    relevant: list[dict[str, Any]] = []
    for item in facts:
        kind = str(item.get("fact_type") or "")
        blob = " ".join(str(item.get(key) or "") for key in ("name", "callee", "module", "file", "path"))
        keep = False
        if kind == "route":
            keep = True
        elif kind in {"import", "function_call", "function"} and (
            _AI_HINT.search(blob) or _AGENT_HINT.search(blob)
        ):
            keep = True
        if keep:
            relevant.append(
                {
                    "fact_type": kind,
                    "file": item.get("file"),
                    "line": item.get("line"),
                    "name": item.get("name") or item.get("handler"),
                    "callee": item.get("callee"),
                    "module": item.get("module"),
                    "path": item.get("path") or item.get("url"),
                    "method": item.get("method"),
                }
            )
        if len(relevant) >= MAX_RELEVANT_FACTS:
            break
    return {
        "summary": summary,
        "relevant_facts": relevant,
        "fact_count": len(facts),
        "relevant_fact_count": len(relevant),
    }


def _select_snippets(
    files: dict[str, str],
    ai: dict[str, Any],
    facts: list[dict[str, Any]],
    *,
    max_snippets: int,
) -> list[dict[str, Any]]:
    targets: list[tuple[str, int]] = []
    for path in ai.get("evidence_files") or []:
        targets.append((str(path), 1))
    for item in ai.get("ai_findings") or []:
        for hit in item.get("evidence") or []:
            if hit.get("file"):
                targets.append((str(hit["file"]), int(hit.get("line") or 1)))
    verification = ai.get("ai_verification") or {}
    for row in list(verification.get("input_flow_evidence") or []) + list(
        verification.get("output_flow_evidence") or []
    ):
        if row.get("file"):
            targets.append((str(row["file"]), int(row.get("line") or 1)))
    for item in facts:
        blob = " ".join(str(item.get(key) or "") for key in ("name", "callee", "file", "module"))
        if item.get("file") and (_AI_HINT.search(blob) or _AGENT_HINT.search(blob)):
            targets.append((str(item["file"]), int(item.get("line") or 1)))

    seen: set[tuple[str, int]] = set()
    snippets: list[dict[str, Any]] = []
    for path, line in targets:
        key = (path, line)
        if key in seen or path not in files:
            continue
        seen.add(key)
        snippets.append(_window(path, files[path], line))
        if len(snippets) >= max_snippets:
            break
    return snippets


def _window(path: str, content: str, line: int) -> dict[str, Any]:
    rows = content.splitlines()
    idx = max(1, line)
    start = max(1, idx - SNIPPET_RADIUS)
    end = min(len(rows), idx + SNIPPET_RADIUS)
    text = "\n".join(rows[start - 1 : end])
    if len(text) > MAX_SNIPPET_CHARS:
        text = text[: MAX_SNIPPET_CHARS] + "\n…[truncated]…"
    return {
        "file": path,
        "start_line": start,
        "end_line": end,
        "focus_line": idx,
        "text": text,
    }


def _call_paths(ai: dict[str, Any], call_graph: Any) -> list[dict[str, Any]]:
    paths: list[dict[str, Any]] = []
    verification = ai.get("ai_verification") or {}
    for row in verification.get("input_flow_evidence") or []:
        if row.get("path"):
            paths.append({"kind": "input_call", "path": row.get("path"), "sink": row.get("sink")})
    for row in verification.get("output_flow_evidence") or []:
        if row.get("path"):
            paths.append({"kind": "output_call", "path": row.get("path"), "sink": row.get("sink")})
    public = call_graph.to_public_dict() if call_graph is not None and hasattr(call_graph, "to_public_dict") else {}
    for edge in (public.get("edges") or [])[:MAX_PATHS]:
        blob = " ".join(str(edge.get(k) or "") for k in ("source", "target", "callee", "to"))
        if _AI_HINT.search(blob) or _AGENT_HINT.search(blob):
            paths.append(
                {
                    "kind": "call_edge",
                    "source": edge.get("source"),
                    "target": edge.get("target"),
                    "confidence": edge.get("confidence"),
                }
            )
        if len(paths) >= MAX_PATHS:
            break
    return paths[:MAX_PATHS]


def _data_flow_paths(ai: dict[str, Any], data_flow: Any) -> list[dict[str, Any]]:
    paths: list[dict[str, Any]] = []
    verification = ai.get("ai_verification") or {}
    for row in verification.get("input_flow_evidence") or []:
        paths.append(
            {
                "kind": "user_to_model",
                "path": row.get("path"),
                "proven": True,
            }
        )
    for row in verification.get("output_flow_evidence") or []:
        paths.append(
            {
                "kind": "model_to_sink",
                "path": row.get("path"),
                "sink": row.get("sink"),
                "proven": True,
            }
        )
    if not paths and data_flow is not None and hasattr(data_flow, "to_public_dict"):
        public = data_flow.to_public_dict()
        for edge in (public.get("edges") or [])[:8]:
            paths.append(
                {
                    "kind": "data_flow_edge",
                    "source": edge.get("source"),
                    "target": edge.get("target"),
                }
            )
    return paths[:MAX_PATHS]


def _deterministic_findings(metrics: dict[str, Any]) -> dict[str, Any]:
    ai = metrics.get("ai_usage") or {}
    fullstack = metrics.get("fullstack") or {}
    return {
        "fullstack": {
            "application_type": fullstack.get("application_type"),
            "frontend": fullstack.get("frontend"),
            "backend": fullstack.get("backend"),
        },
        "ai_usage": {
            "detected": ai.get("detected"),
            "providers": ai.get("providers"),
            "dependency_detected": ai.get("dependency_detected"),
            "import_detected": ai.get("import_detected"),
            "client_detected": ai.get("client_detected"),
            "model_invocation_detected": ai.get("model_invocation_detected"),
            "user_input_reaches_model": ai.get("user_input_reaches_model"),
            "model_output_used": ai.get("model_output_used"),
            "ai_integration_type": ai.get("ai_integration_type"),
            "ai_dependencies_found": ai.get("ai_dependencies_found") or [],
            "agent_frameworks_found": ai.get("agent_frameworks_found") or [],
            "confidence": ai.get("confidence"),
            "ai_findings": [
                {
                    "rule_id": item.get("rule_id"),
                    "status": item.get("status"),
                    "confidence": item.get("confidence"),
                }
                for item in (ai.get("ai_findings") or [])[:12]
            ],
        },
    }
