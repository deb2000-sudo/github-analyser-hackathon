"""Phase 16: explainable analysis result. Does not invent certainty."""

from __future__ import annotations

from typing import Any

from app import __version__
from app.analysis.ai_fake import RULE_HARDCODED

_LABEL_CONFIDENCE = {"low": 0.4, "medium": 0.7, "high": 0.9}
_PROVIDER_LABELS = {
    "gemini": "Gemini",
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "groq": "Groq",
    "mistral": "Mistral",
    "cohere": "Cohere",
    "huggingface": "Hugging Face",
    "vertex": "Vertex AI",
}
_DYNAMIC_IMPORT_CALLEES = {
    "__import__",
    "import_module",
    "importlib.import_module",
    "importlib.__import__",
}
_GATE_LIMITATIONS = {
    "repository_is_private": "Repository is private — analysis was not run.",
    "repository_not_found_or_inaccessible": (
        "Repository is not publicly accessible — analysis was not run."
    ),
}
_MAX_EVIDENCE = 60
_MAX_LIMITATIONS = 20
_NOT_ANALYZED = "not_analyzed"


def build_analysis_result(
    *,
    access: dict[str, Any],
    repository: dict[str, Any],
    metrics: dict[str, Any] | None = None,
    scoring: dict[str, Any] | None = None,
    verdict: dict[str, Any] | None = None,
    code_facts: Any | None = None,
    context: dict[str, Any] | None = None,
    llm_plan: Any | None = None,
    analysis: dict[str, Any] | None = None,
    gated: bool = False,
    gate_reason: str | None = None,
    timings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the public job result. Legacy keys are preserved."""
    metrics = metrics or {}
    scoring = scoring or {}
    verdict = verdict or {}
    if gated:
        architecture = _empty_architecture()
        frontend_backend = _empty_frontend_backend()
        ai = _empty_ai()
        agents = _empty_agents()
        solution_fit = _empty_solution_fit()
        evidence: list[dict[str, Any]] = []
    else:
        architecture = _architecture(metrics, verdict, code_facts)
        frontend_backend = _frontend_backend(metrics)
        ai = _ai_summary(metrics, verdict)
        agents = _agents_summary(metrics)
        solution_fit = _solution_fit_summary(metrics)
        evidence = _collect_evidence(metrics, code_facts)
    limitations = _collect_limitations(
        metrics,
        code_facts,
        gated=gated,
        gate_reason=gate_reason,
        llm_plan=llm_plan,
    )
    payload = {
        "access": access,
        "repository": dict(repository),
        "architecture": architecture,
        "frontend_backend": frontend_backend,
        "ai": ai,
        "agents": agents,
        "solution_fit": solution_fit,
        "metrics": metrics,
        "scoring": scoring,
        "evidence": evidence,
        "limitations": limitations,
        "metadata": {
            "version": __version__,
            "gated": gated,
            "commit_sha": repository.get("commit_sha"),
            "llm_invoked": bool(getattr(llm_plan, "should_run", False)),
            "llm_reasons": list(getattr(llm_plan, "reasons", None) or []),
            "uncertain_results_are_not_proven": True,
            "timing": _timing_payload(timings),
        },
        # Legacy compatibility
        "repo": dict(repository),
        "context": context,
    }
    if verdict:
        payload["verdict"] = verdict
    if analysis is not None:
        payload["analysis"] = analysis
    return payload


def _timing_payload(timings: dict[str, Any] | None) -> dict[str, int]:
    keys = ("inventory_ms", "parse_ms", "graph_ms", "metrics_ms", "llm_ms", "total_ms")
    out = {key: 0 for key in keys}
    if timings:
        for key, value in timings.items():
            if isinstance(value, (int, float)):
                out[str(key)] = int(value)
    return out


def _empty_architecture() -> dict[str, Any]:
    return {
        "application_type": "unknown",
        "frontend": {"detected": False, "framework": None},
        "backend": {"detected": False, "framework": None},
        "layout": None,
        "confidence": 0.0,
    }


def _empty_frontend_backend() -> dict[str, Any]:
    return {
        "connected": None,
        "connection_count": 0,
        "backend_route_count": 0,
        "frontend_request_count": 0,
        "unmatched_frontend": 0,
        "unmatched_backend": 0,
        "method_mismatches": 0,
        "unknown": True,
    }


def _empty_ai() -> dict[str, Any]:
    return {
        "detected": False,
        "providers": [],
        "integration_level": 0,
        "classification": _NOT_ANALYZED,
        "model_invocation_detected": False,
        "user_input_reaches_model": False,
        "model_output_used": False,
        "hardcoded_response_detected": False,
        "confidence": 0.0,
    }


def _empty_agents() -> dict[str, Any]:
    return {
        "classification": _NOT_ANALYZED,
        "has_real_orchestration": False,
        "agent_count": 0,
        "evidence_ids": [],
        "confidence": 0.0,
        "status": "skipped",
    }


def _empty_solution_fit() -> dict[str, Any]:
    return {
        "implementation_matches_claim": None,
        "context_relevant": None,
        "alignment_score": None,
        "relevance_score": None,
        "verified_features": [],
        "unsupported_features": [],
        "partial_features": [],
        "evidence_ids": [],
        "confidence": 0.0,
        "status": "skipped",
    }


def _architecture(
    metrics: dict[str, Any],
    verdict: dict[str, Any],
    code_facts: Any | None,
) -> dict[str, Any]:
    fullstack = metrics.get("fullstack") or {}
    conclusion = _conclusion(verdict, "APPLICATION_TYPE")
    structure = getattr(code_facts, "structure", None) or {}
    application_type = (
        (conclusion or {}).get("classification")
        or fullstack.get("application_type")
        or structure.get("layout")
        or "unknown"
    )
    confidence = (conclusion or {}).get("confidence")
    if confidence is None:
        confidence = 0.3 if application_type == "unknown" else 0.72
    return {
        "application_type": application_type,
        "frontend": fullstack.get("frontend") or {"detected": False, "framework": None},
        "backend": fullstack.get("backend") or {"detected": False, "framework": None},
        "layout": structure.get("layout")
        or (getattr(code_facts, "inventory", None) or {}).get("layout"),
        "confidence": float(confidence),
    }


def _frontend_backend(metrics: dict[str, Any]) -> dict[str, Any]:
    data = metrics.get("frontend_backend") or {}
    if not data:
        return {
            "connected": None,
            "connection_count": 0,
            "backend_route_count": 0,
            "frontend_request_count": 0,
            "unmatched_frontend": 0,
            "unmatched_backend": 0,
            "method_mismatches": 0,
            "unknown": True,
        }
    connected = data.get("connected")
    if connected not in (True, False, "unknown"):
        connected = "unknown"
    return {
        "connected": connected,
        "connection_count": len(data.get("connections") or []),
        "backend_route_count": len(data.get("backend_routes") or []),
        "frontend_request_count": len(data.get("frontend_requests") or []),
        "unmatched_frontend": len(data.get("unmatched_frontend") or []),
        "unmatched_backend": len(data.get("unmatched_backend") or []),
        "method_mismatches": len(data.get("method_mismatches") or []),
        "unknown": connected == "unknown",
    }


def _ai_summary(metrics: dict[str, Any], verdict: dict[str, Any]) -> dict[str, Any]:
    ai = metrics.get("ai_usage") or {}
    findings = list(ai.get("ai_findings") or [])
    hardcoded = any(
        str(item.get("rule_id")) == RULE_HARDCODED and item.get("status") == "failed"
        for item in findings
    )
    raw_class = verdict.get("ai_classification")
    classification = str(raw_class).lower() if raw_class else "no_ai"
    level = verdict.get("ai_level")
    if level is None:
        level = int(ai.get("integration_level") or 0)
    confidence = verdict.get("confidence")
    if not isinstance(confidence, (int, float)):
        verification = ai.get("ai_verification") or {}
        confidence = verification.get("confidence")
    if not isinstance(confidence, (int, float)):
        confidence = _LABEL_CONFIDENCE.get(str(ai.get("confidence") or "").lower(), 0.3)
    detected = bool(ai.get("detected")) or int(level) > 0 or classification not in {"no_ai", ""}
    return {
        "detected": detected,
        "providers": list(ai.get("providers") or []),
        "integration_level": int(level),
        "classification": classification,
        "model_invocation_detected": bool(ai.get("model_invocation_detected")),
        "user_input_reaches_model": bool(ai.get("user_input_reaches_model")),
        "model_output_used": bool(ai.get("model_output_used")),
        "hardcoded_response_detected": hardcoded,
        "confidence": round(float(confidence), 4),
    }


def _agents_summary(metrics: dict[str, Any]) -> dict[str, Any]:
    data = metrics.get("agent_analysis") or {}
    if not data:
        return {
            "classification": "NO_AGENT",
            "has_real_orchestration": False,
            "agent_count": 0,
            "evidence_ids": [],
            "confidence": 0.3,
            "status": None,
        }
    classification = str(data.get("classification") or "NO_AGENT")
    confidence = data.get("confidence")
    numeric = (
        _LABEL_CONFIDENCE.get(str(confidence).lower())
        if isinstance(confidence, str)
        else confidence
    )
    if not isinstance(numeric, (int, float)):
        numeric = 0.3 if data.get("status") == "skipped" else 0.7
    return {
        "classification": classification,
        "has_real_orchestration": bool(data.get("has_real_orchestration")),
        "agent_count": int(data.get("agent_count") or 0),
        "evidence_ids": list(data.get("evidence_ids") or []),
        "confidence": round(float(numeric), 4),
        "status": data.get("status"),
    }


def _solution_fit_summary(metrics: dict[str, Any]) -> dict[str, Any]:
    data = metrics.get("solution_fit") or {}
    confidence = data.get("confidence")
    numeric = (
        _LABEL_CONFIDENCE.get(str(confidence).lower())
        if isinstance(confidence, str)
        else confidence
    )
    if not isinstance(numeric, (int, float)):
        numeric = 0.3 if data else 0.0
    match = data.get("implementation_matches_claim")
    return {
        "implementation_matches_claim": match if isinstance(match, bool) else None,
        "context_relevant": data.get("context_relevant")
        if isinstance(data.get("context_relevant"), bool)
        else None,
        "alignment_score": data.get("alignment_score"),
        "relevance_score": data.get("relevance_score"),
        "verified_features": data.get("verified_features") or [],
        "unsupported_features": data.get("unsupported_features") or [],
        "partial_features": data.get("partial_features") or [],
        "evidence_ids": data.get("evidence_ids") or [],
        "confidence": round(float(numeric), 4) if data else 0.0,
        "status": data.get("status"),
    }


def _collect_evidence(metrics: dict[str, Any], code_facts: Any | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seq = 1

    def add(
        *,
        rule_id: str,
        description: str,
        file: str | None = None,
        line: Any = None,
        symbol: str | None = None,
    ) -> None:
        nonlocal seq
        resolved_line = _coerce_line(line)
        if resolved_line is None:
            resolved_line = _lookup_line(code_facts, file, symbol)
        item = {
            "id": f"ev_{seq:03d}",
            "rule_id": rule_id,
            "file": file,
            "line": resolved_line,
            "symbol": symbol,
            "description": description,
        }
        rows.append(item)
        seq += 1

    ai = metrics.get("ai_usage") or {}
    provider_label = _provider_label(ai.get("providers"))
    for row in ai.get("input_flow_evidence") or []:
        symbol = str(row.get("route") or row.get("source") or "input")
        add(
            rule_id="AI.DYNAMIC_INPUT",
            file=row.get("file"),
            symbol=symbol,
            description=f"Request message reaches {provider_label} model invocation",
            line=row.get("line"),
        )
    for row in ai.get("output_flow_evidence") or []:
        add(
            rule_id="AI.OUTPUT_USED",
            file=row.get("file"),
            symbol=str(row.get("sink") or row.get("route") or "output"),
            description="Model output is consumed by the application",
            line=row.get("line"),
        )
    if ai.get("model_invocation_detected") and not (ai.get("input_flow_evidence") or []):
        files = ai.get("evidence_files") or []
        add(
            rule_id="AI.INVOCATION",
            file=files[0] if files else None,
            symbol=(ai.get("providers") or ["model"])[0],
            description="Model invocation detected in source facts",
        )
    for finding in ai.get("ai_findings") or []:
        hit = (finding.get("evidence") or [{}])[0] or {}
        add(
            rule_id=str(finding.get("rule_id") or "AI.FINDING"),
            file=hit.get("file"),
            line=hit.get("line"),
            symbol=str(finding.get("rule_id") or "").split(".")[-1].lower(),
            description=_finding_description(finding),
        )
    agent = metrics.get("agent_analysis") or {}
    for item in (agent.get("agent_evidence") or {}).get("items") or []:
        add(
            rule_id=f"AGT.{item.get('kind') or 'SIGNAL'}",
            file=item.get("file"),
            line=item.get("line"),
            symbol=str(item.get("detail") or item.get("kind") or "agent"),
            description=str(item.get("detail") or "Agent-related deterministic signal"),
        )
        if len(rows) >= _MAX_EVIDENCE:
            break
    connectivity = metrics.get("frontend_backend") or {}
    for conn in (connectivity.get("connections") or [])[:8]:
        add(
            rule_id="FB.CONNECTED",
            file=conn.get("frontend_file"),
            line=conn.get("frontend_line"),
            symbol=str(conn.get("path") or "http"),
            description=f"{conn.get('method')} {conn.get('path')} matches a backend route",
        )
    if not (connectivity.get("connections") or []):
        for route in (connectivity.get("backend_routes") or [])[:6]:
            add(
                rule_id="HTTP.ROUTE",
                file=route.get("file"),
                line=route.get("line"),
                symbol=str(route.get("handler") or route.get("path") or "route"),
                description=f"Backend route {route.get('method')} {route.get('path')}",
            )
    return rows[:_MAX_EVIDENCE]


def _provider_label(providers: Any) -> str:
    names = [str(p) for p in (providers or []) if p]
    if not names:
        return "model"
    key = names[0].lower()
    return _PROVIDER_LABELS.get(key, names[0])


def _coerce_line(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _lookup_line(code_facts: Any | None, file: str | None, symbol: str | None) -> int | None:
    if not code_facts or not symbol:
        return None
    facts = getattr(code_facts, "source_facts", None) or []
    for fact in facts:
        if file and fact.get("file") != file:
            continue
        if fact.get("name") == symbol or fact.get("scope") == symbol or fact.get("handler") == symbol:
            line = _coerce_line(fact.get("line"))
            if line is not None:
                return line
    return None


def _finding_description(finding: dict[str, Any]) -> str:
    rule = str(finding.get("rule_id") or "finding")
    status = finding.get("status") or "noted"
    return f"{rule} ({status})"


def _collect_limitations(
    metrics: dict[str, Any],
    code_facts: Any | None,
    *,
    gated: bool,
    gate_reason: str | None,
    llm_plan: Any | None,
) -> list[str]:
    notes: list[str] = []
    if gated:
        notes.append(
            _GATE_LIMITATIONS.get(
                str(gate_reason or ""),
                "Repository is not publicly accessible — analysis was not run.",
            )
        )
        return notes[:_MAX_LIMITATIONS]

    for warning in getattr(code_facts, "parse_warnings", None) or []:
        kind = str(warning.get("kind") or "parse_warning")
        path = warning.get("file") or "a file"
        if kind == "skipped_too_large":
            notes.append(f"Source file {path} was skipped because it exceeded the parse size limit")
        elif kind == "parse_error":
            notes.append(f"Parse error in {path}: {warning.get('message') or 'could not parse'}")
        else:
            notes.append(f"{kind} in {path}")

    if _has_unresolved_dynamic_import(code_facts):
        notes.append("Dynamic import could not be resolved")
    if _has_unresolved_dynamic_call(code_facts):
        notes.append("Dynamic call could not be resolved")

    dfg = getattr(code_facts, "data_flow", None)
    if dfg is not None and hasattr(dfg, "graph"):
        if any(data.get("kind") == "unknown" for _, data in dfg.graph.nodes(data=True)):
            notes.append("A dynamic value flow could not be proven and remains unknown")

    fb = metrics.get("frontend_backend") or {}
    if fb.get("connected") == "unknown":
        notes.append("Frontend↔backend connectivity could not be proven from static URLs")
    if _frontend_url_is_dynamic(fb):
        notes.append("Frontend URL constructed at runtime")

    ai = metrics.get("ai_usage") or {}
    if ai.get("model_invocation_detected") and not ai.get("user_input_reaches_model"):
        notes.append("User input reaching the model was not statically proven")
    if ai.get("model_invocation_detected") and not ai.get("model_output_used"):
        notes.append("Model output consumption was not statically proven")
    verification = ai.get("ai_verification") or {}
    if isinstance(verification.get("confidence"), (int, float)) and float(verification["confidence"]) < 0.75:
        notes.append("AI flow confidence is below the static proof threshold")

    agent = metrics.get("agent_analysis") or {}
    if agent.get("status") == "skipped":
        notes.append(str(agent.get("reasoning") or "Agent analysis was skipped"))
    fit = metrics.get("solution_fit") or {}
    if fit.get("status") == "skipped":
        notes.append(str(fit.get("reasoning") or "Solution fit was skipped"))

    inventory = getattr(code_facts, "inventory", None) or {}
    if inventory.get("truncated"):
        notes.append("File inventory was truncated; some paths were not analyzed")

    del llm_plan  # recorded in metadata; skipping Gemini is not a defect

    out: list[str] = []
    seen: set[str] = set()
    for note in notes:
        text = str(note).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
        if len(out) >= _MAX_LIMITATIONS:
            break
    return out


def _has_unresolved_dynamic_import(code_facts: Any | None) -> bool:
    if code_facts is None:
        return False
    for fact in getattr(code_facts, "source_facts", None) or []:
        if fact.get("fact_type") != "function_call":
            continue
        callee = str(fact.get("callee") or "")
        if callee in _DYNAMIC_IMPORT_CALLEES or callee.endswith(".import_module"):
            return True
    graph = getattr(code_facts, "call_graph", None)
    public = graph.to_public_dict() if graph is not None and hasattr(graph, "to_public_dict") else {}
    for edge in public.get("edges") or []:
        target = str(edge.get("target") or "")
        if not target.startswith("unresolved:"):
            continue
        callee = target.split(":", 1)[-1]
        if callee in _DYNAMIC_IMPORT_CALLEES:
            return True
    return False


def _has_unresolved_dynamic_call(code_facts: Any | None) -> bool:
    graph = getattr(code_facts, "call_graph", None)
    if graph is None or not hasattr(graph, "to_public_dict"):
        return False
    for edge in graph.to_public_dict().get("edges") or []:
        target = str(edge.get("target") or "")
        if not target.startswith("unresolved:"):
            continue
        callee = target.split(":", 1)[-1]
        if callee not in _DYNAMIC_IMPORT_CALLEES:
            return True
    return False


def _frontend_url_is_dynamic(fb: dict[str, Any]) -> bool:
    for req in fb.get("frontend_requests") or []:
        if req.get("dynamic") or req.get("dynamic_base") or req.get("resolved") is False:
            return True
    return any(
        unmatched.get("reason") == "unresolved_url"
        for unmatched in fb.get("unmatched_frontend") or []
    )


def _conclusion(verdict: dict[str, Any], conclusion_id: str) -> dict[str, Any] | None:
    for item in verdict.get("conclusions") or []:
        if item.get("id") == conclusion_id:
            return item
    return None
