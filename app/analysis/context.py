"""Shared analysis view of one repository. Built once; metrics only read it.

Keep this module off app.analysis.__init__ (import-light package init).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

from app.analysis.ai_evidence import AiEvidence, detect_ai_evidence
from app.analysis.ai_fake import detect_fake_ai
from app.analysis.ai_flow import analyze_ai_flow
from app.analysis.call_graph import CallGraph
from app.analysis.data_flow import DataFlowGraph
from app.analysis.facts import (
    CodeFacts,
    assemble_code_facts,
    run_graph_stage,
    run_inventory_stage,
    run_parse_stage,
)
from app.analysis.http_io import detect_http_io
from app.analysis.inventory import FileInventory

if TYPE_CHECKING:
    from app.github.client import RepoSnapshot

TIMING_KEYS = (
    "inventory_ms",
    "parse_ms",
    "graph_ms",
    "metrics_ms",
    "llm_ms",
    "total_ms",
)


def _ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))


def empty_timings() -> dict[str, int]:
    return {key: 0 for key in TIMING_KEYS}


@dataclass
class AnalysisContext:
    """One parsed repository, reused by every metric."""

    repository: dict[str, Any]
    inventory: dict[str, Any]
    manifests: dict[str, Any]
    code_facts: CodeFacts
    routes: list[dict[str, Any]]
    http_calls: list[dict[str, Any]]
    call_graph: CallGraph
    data_flow: DataFlowGraph
    ai_findings: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    file_inventory: FileInventory | None = None
    structure: dict[str, Any] = field(default_factory=dict)
    ai_evidence: AiEvidence | None = None
    ai_verification: dict[str, Any] = field(default_factory=dict)
    timings: dict[str, int] = field(default_factory=empty_timings)

    def timing_dict(self) -> dict[str, int]:
        out = empty_timings()
        out.update({k: int(self.timings.get(k) or 0) for k in TIMING_KEYS})
        for key, value in self.timings.items():
            if key not in out and isinstance(value, (int, float)):
                out[key] = int(value)
        return out

    @classmethod
    def from_code_facts(
        cls,
        facts: CodeFacts,
        snapshot: RepoSnapshot,
        *,
        submission_context: dict[str, Any] | None = None,
        timings: dict[str, int] | None = None,
    ) -> AnalysisContext:
        """Reuse already-parsed facts. Does not parse source files again."""
        http_io = detect_http_io(list(facts.source_facts or []))
        routes = list(http_io.get("backend_routes") or [])
        http_calls = list(http_io.get("frontend_requests") or [])
        ai_evidence = detect_ai_evidence(
            getattr(facts, "source_facts", None) or [],
            manifests=getattr(snapshot, "package_manifests", None),
            parsed_manifests=getattr(facts, "manifests", None),
            file_contents=getattr(snapshot, "file_contents", None),
            submission_context=submission_context,
        )
        verification = analyze_ai_flow(
            getattr(facts, "source_facts", None) or [],
            getattr(facts, "call_graph", None),
            getattr(facts, "data_flow", None),
        )
        findings = detect_fake_ai(
            getattr(facts, "source_facts", None) or [],
            evidence=ai_evidence,
            verification=verification,
            call_graph=getattr(facts, "call_graph", None),
            manifests=getattr(snapshot, "package_manifests", None),
        )
        merged = empty_timings()
        if timings:
            merged.update({k: int(v) for k, v in timings.items() if isinstance(v, (int, float))})
        return cls(
            repository=dict(facts.repository or {}),
            inventory=dict(facts.inventory or {}),
            manifests=dict(facts.manifests or {}),
            code_facts=facts,
            routes=routes,
            http_calls=http_calls,
            call_graph=facts.call_graph,
            data_flow=facts.data_flow,
            ai_findings=list(findings or []),
            evidence=_seed_evidence(routes, http_calls, ai_evidence, findings),
            file_inventory=facts.file_inventory,
            structure=dict(facts.structure or {}),
            ai_evidence=ai_evidence,
            ai_verification=verification,
            timings=merged,
        )


def build_analysis_context(
    snapshot: RepoSnapshot,
    *,
    submission_context: dict[str, Any] | None = None,
) -> AnalysisContext:
    """Repository → inventory → parse → facts → graphs → AnalysisContext."""
    started = time.perf_counter()

    inv_started = time.perf_counter()
    stage = run_inventory_stage(snapshot)
    inventory_ms = _ms(inv_started)

    parse_started = time.perf_counter()
    source_facts, parse_warnings = run_parse_stage(stage)
    parse_ms = _ms(parse_started)

    graph_started = time.perf_counter()
    call_graph, data_flow = run_graph_stage(source_facts)
    graph_ms = _ms(graph_started)

    facts = assemble_code_facts(stage, source_facts, parse_warnings, call_graph, data_flow)
    timings = empty_timings()
    timings["inventory_ms"] = inventory_ms
    timings["parse_ms"] = parse_ms
    timings["graph_ms"] = graph_ms
    timings["total_ms"] = _ms(started)
    return AnalysisContext.from_code_facts(
        facts,
        snapshot,
        submission_context=submission_context,
        timings=timings,
    )


def get_analysis(ctx: Any) -> AnalysisContext:
    """Return the shared context, building it at most once per MetricContext."""
    existing = getattr(ctx, "analysis", None)
    if existing is not None:
        return existing
    extras = getattr(ctx, "extras", None)
    if not isinstance(extras, dict):
        extras = {}
        try:
            ctx.extras = extras
        except Exception:
            extras = {}
    cached = extras.get("analysis_context")
    if cached is not None:
        ctx.analysis = cached
        extras.setdefault("code_facts", cached.code_facts)
        extras["skip_file_fetch"] = True
        return cached

    facts = extras.get("code_facts")
    snapshot = getattr(ctx, "snapshot", None)
    if isinstance(facts, CodeFacts) and snapshot is not None:
        built = AnalysisContext.from_code_facts(
            facts,
            snapshot,
            submission_context=extras.get("submission_context"),
        )
    else:
        built = build_analysis_context(
            snapshot,
            submission_context=extras.get("submission_context"),
        )
    ctx.analysis = built
    extras["analysis_context"] = built
    extras["code_facts"] = built.code_facts
    extras["skip_file_fetch"] = True
    extras.setdefault("code_facts_summary", built.code_facts.summary())
    return built


def attach_analysis(ctx: Any, analysis: AnalysisContext) -> AnalysisContext:
    """Bind a prebuilt context onto a metric context (pipeline path)."""
    ctx.analysis = analysis
    extras = getattr(ctx, "extras", None)
    if isinstance(extras, dict):
        extras["analysis_context"] = analysis
        extras["code_facts"] = analysis.code_facts
        extras["code_facts_summary"] = analysis.code_facts.summary()
        extras["skip_file_fetch"] = True
    return analysis


def _seed_evidence(
    routes: list[dict[str, Any]],
    http_calls: list[dict[str, Any]],
    ai_evidence: AiEvidence,
    ai_findings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seq = 1

    def add(*, rule_id: str, description: str, file: Any = None, line: Any = None, symbol: Any = None) -> None:
        nonlocal seq
        rows.append(
            {
                "id": f"ctx_{seq:03d}",
                "rule_id": rule_id,
                "file": file,
                "line": line if isinstance(line, int) else None,
                "symbol": symbol,
                "description": description,
            }
        )
        seq += 1

    for route in routes[:20]:
        add(
            rule_id="HTTP.ROUTE",
            file=route.get("file"),
            line=route.get("line"),
            symbol=str(route.get("handler") or route.get("path") or "route"),
            description=f"Backend route {route.get('method')} {route.get('path')}",
        )
    for call in http_calls[:20]:
        add(
            rule_id="HTTP.CLIENT",
            file=call.get("file"),
            line=call.get("line"),
            symbol=str(call.get("path") or call.get("callee") or "http"),
            description=f"Frontend HTTP {call.get('method')} {call.get('raw') or call.get('path')}",
        )
    for item in ai_evidence.evidence[:20]:
        files = item.get("files") or []
        add(
            rule_id=f"AI.{str(item.get('kind') or 'EVIDENCE').upper()}",
            file=files[0] if files else None,
            symbol=str(item.get("provider") or "ai"),
            description=str((item.get("details") or ["AI evidence"])[0]),
        )
    for finding in ai_findings[:20]:
        hit = (finding.get("evidence") or [{}])[0] or {}
        add(
            rule_id=str(finding.get("rule_id") or "AI.FINDING"),
            file=hit.get("file"),
            line=hit.get("line"),
            symbol=str(finding.get("rule_id") or "").split(".")[-1].lower(),
            description=f"{finding.get('rule_id')} ({finding.get('status')})",
        )
    return rows
