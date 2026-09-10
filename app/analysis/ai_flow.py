"""Phase 9: prove user data → model invocation and model output → app sink.

Uses route facts, the call graph, the data-flow graph, and Phase 8 invocation rules.
Never invents a flow. Unproven paths stay false.
"""

from __future__ import annotations

from typing import Any

import networkx as nx

from app.analysis.ai_evidence import list_model_invocations
from app.analysis.call_graph import CallGraph
from app.analysis.data_flow import DataFlowConfig, DataFlowGraph, build_data_flow
from app.analysis.source_models import MODULE_SCOPE

_APP_SINK_SUFFIXES = (
    "jsonify",
    "JSONResponse",
    "PlainTextResponse",
    "StreamingResponse",
    "res.json",
    "res.send",
    "response.json",
    "response.send",
    "insert_one",
    "insert_many",
    "insertOne",
    "put_item",
    "addDoc",
    "setDoc",
    "write_text",
    "write_bytes",
    "writeFile",
    "writeFileSync",
    "fs.writeFile",
)


def analyze_ai_flow(
    source_facts: list[dict[str, Any]],
    call_graph: CallGraph | None = None,
    data_flow: DataFlowGraph | None = None,
) -> dict[str, Any]:
    """Return Phase 9 verification. `data_flow` is unused; a sink-aware graph is rebuilt."""
    del data_flow
    invocations = list_model_invocations(source_facts)
    empty = _payload(
        model_invocation_detected=False,
        user_input_reaches_model=False,
        model_output_used=False,
        input_flow_evidence=[],
        output_flow_evidence=[],
        confidence=0.9,
    )
    if not invocations:
        return empty

    handlers = _route_handlers(source_facts)
    ai_callees = tuple(dict.fromkeys(str(item["callee"]) for item in invocations))
    app_callees = tuple(_app_sink_callees(source_facts))
    dfg = build_data_flow(
        source_facts,
        config=DataFlowConfig(sink_callees=tuple(dict.fromkeys([*ai_callees, *app_callees]))),
    )
    _tag_sinks(dfg, set(ai_callees), set(app_callees))
    _attach_ai_outputs(dfg, source_facts, invocations)

    input_evidence = _input_evidence(dfg, call_graph, invocations, handlers)
    output_evidence = _output_evidence(dfg, invocations, handlers)
    user_in = bool(input_evidence)
    output_used = bool(output_evidence)
    confidence = _confidence(user_in, output_used)

    return _payload(
        model_invocation_detected=True,
        user_input_reaches_model=user_in,
        model_output_used=output_used,
        input_flow_evidence=input_evidence,
        output_flow_evidence=output_evidence,
        confidence=confidence,
    )


def _payload(
    *,
    model_invocation_detected: bool,
    user_input_reaches_model: bool,
    model_output_used: bool,
    input_flow_evidence: list[dict[str, Any]],
    output_flow_evidence: list[dict[str, Any]],
    confidence: float,
) -> dict[str, Any]:
    return {
        "model_invocation_detected": model_invocation_detected,
        "user_input_reaches_model": user_input_reaches_model,
        "model_output_used": model_output_used,
        "input_flow_evidence": input_flow_evidence,
        "output_flow_evidence": output_flow_evidence,
        "confidence": confidence,
    }


def _route_handlers(source_facts: list[dict[str, Any]]) -> set[tuple[str, str]]:
    handlers: set[tuple[str, str]] = set()
    for item in source_facts:
        if item.get("fact_type") != "route":
            continue
        handler = str(item.get("handler") or "")
        file = str(item.get("file") or "")
        if handler:
            handlers.add((file, handler))
    return handlers


def _app_sink_callees(source_facts: list[dict[str, Any]]) -> list[str]:
    found: list[str] = []
    for item in source_facts:
        if item.get("fact_type") != "function_call":
            continue
        callee = str(item.get("callee") or "")
        if any(callee == suffix or callee.endswith("." + suffix) for suffix in _APP_SINK_SUFFIXES):
            found.append(callee)
    return found


def _tag_sinks(dfg: DataFlowGraph, ai_callees: set[str], app_callees: set[str]) -> None:
    for _, data in dfg.graph.nodes(data=True):
        if data.get("kind") != "sink":
            continue
        ref = str(data.get("ref") or data.get("name") or "")
        if ref in ai_callees:
            data["role"] = "model"
        elif ref in app_callees:
            data["role"] = "app"


def _attach_ai_outputs(
    dfg: DataFlowGraph,
    source_facts: list[dict[str, Any]],
    invocations: list[dict[str, Any]],
) -> None:
    assigns = [f for f in source_facts if f.get("fact_type") == "assignment"]
    returns = [f for f in source_facts if f.get("fact_type") == "return"]
    for item in invocations:
        file = str(item.get("file") or "")
        scope = str(item.get("scope") or MODULE_SCOPE)
        callee = str(item.get("callee") or "")
        line = item.get("line")
        out_id = _ai_out(file, scope, callee)
        dfg.add_node(
            out_id,
            kind="ai_output",
            name=callee,
            ref="ai_output",
            file=file,
            scope=scope,
            callee=callee,
        )
        for assign in assigns:
            if assign.get("file") != file or assign.get("scope") != scope:
                continue
            if str(assign.get("value") or "") != callee:
                continue
            if assign.get("line") not in {None, line} and line is not None:
                continue
            target = str(assign.get("target") or "")
            if not target:
                continue
            target_id = f"{file}::{scope}::{target}"
            dfg.add_node(target_id, kind="value", name=target, ref=target, file=file, scope=scope)
            dfg.add_flow(out_id, target_id)
        for ret in returns:
            if ret.get("file") != file or ret.get("scope") != scope:
                continue
            if str(ret.get("value") or "") != callee:
                continue
            ret_id = f"{file}::{scope}::<return>"
            dfg.add_node(ret_id, kind="value", name="<return>", ref="<return>", file=file, scope=scope)
            dfg.add_flow(out_id, ret_id)


def _input_evidence(
    dfg: DataFlowGraph,
    call_graph: CallGraph | None,
    invocations: list[dict[str, Any]],
    handlers: set[tuple[str, str]],
) -> list[dict[str, Any]]:
    sources = [n for n, data in dfg.graph.nodes(data=True) if data.get("kind") == "source"]
    sinks = [
        n
        for n, data in dfg.graph.nodes(data=True)
        if data.get("kind") == "sink" and data.get("role") == "model"
    ]
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for src in sources:
        for sink in sinks:
            if not nx.has_path(dfg.graph, src, sink):
                continue
            key = (src, sink)
            if key in seen:
                continue
            seen.add(key)
            path = _one_path(dfg.graph, src, sink)
            sink_data = dfg.graph.nodes[sink]
            src_data = dfg.graph.nodes[src]
            route = _route_for(src_data.get("file"), src_data.get("scope"), handlers)
            call_paths = _call_paths(call_graph, src_data.get("scope"), sink_data.get("ref") or sink_data.get("name"))
            rows.append(
                {
                    "source": src_data.get("ref") or src_data.get("name"),
                    "sink": sink_data.get("ref") or sink_data.get("name"),
                    "file": src_data.get("file"),
                    "route": route,
                    "path": _labels(dfg, path),
                    "nodes": path,
                    "call_graph_paths": call_paths[:3],
                }
            )
            if len(rows) >= 12:
                return rows
    return rows


def _output_evidence(
    dfg: DataFlowGraph,
    invocations: list[dict[str, Any]],
    handlers: set[tuple[str, str]],
) -> list[dict[str, Any]]:
    outputs = [n for n, data in dfg.graph.nodes(data=True) if data.get("kind") == "ai_output"]
    targets = _app_targets(dfg, handlers)
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for out in outputs:
        for target in targets:
            if out == target or not nx.has_path(dfg.graph, out, target):
                continue
            key = (out, target)
            if key in seen:
                continue
            seen.add(key)
            path = _one_path(dfg.graph, out, target)
            out_data = dfg.graph.nodes[out]
            tgt_data = dfg.graph.nodes[target]
            rows.append(
                {
                    "source": out_data.get("name") or "ai_output",
                    "sink": _sink_label(tgt_data),
                    "file": tgt_data.get("file") or out_data.get("file"),
                    "route": _route_for(tgt_data.get("file"), tgt_data.get("scope"), handlers),
                    "path": _labels(dfg, path),
                    "nodes": path,
                }
            )
            if len(rows) >= 12:
                return rows
    return rows


def _app_targets(dfg: DataFlowGraph, handlers: set[tuple[str, str]]) -> list[str]:
    targets: list[str] = []
    for node, data in dfg.graph.nodes(data=True):
        if data.get("kind") == "sink" and data.get("role") == "app":
            targets.append(node)
            continue
        if data.get("ref") == "<return>" or data.get("name") == "<return>":
            pair = (str(data.get("file") or ""), str(data.get("scope") or ""))
            if pair in handlers:
                targets.append(node)
    return targets


def _sink_label(data: dict[str, Any]) -> str:
    if data.get("ref") == "<return>" or data.get("name") == "<return>":
        return "http_response"
    if data.get("role") == "app":
        return str(data.get("ref") or data.get("name") or "app_sink")
    return str(data.get("ref") or data.get("name") or "sink")


def _route_for(file: Any, scope: Any, handlers: set[tuple[str, str]]) -> str | None:
    pair = (str(file or ""), str(scope or ""))
    if pair in handlers:
        return str(scope)
    return None


def _call_paths(call_graph: CallGraph | None, handler: Any, ai_callee: Any) -> list[list[str]]:
    if call_graph is None or not handler or not ai_callee:
        return []
    try:
        paths = call_graph.find_paths(str(handler), str(ai_callee), max_depth=8)
    except Exception:
        return []
    labels = []
    for path in paths[:3]:
        labels.append([str(call_graph.graph.nodes[n].get("name") or n) for n in path])
    return labels


def _one_path(graph: nx.DiGraph, src: str, dst: str) -> list[str]:
    try:
        return next(nx.all_simple_paths(graph, src, dst, cutoff=12))
    except StopIteration:
        return [src, dst]


def _labels(dfg: DataFlowGraph, nodes: list[str]) -> list[str]:
    labels: list[str] = []
    for node in nodes:
        data = dfg.graph.nodes.get(node) or {}
        label = str(data.get("ref") or data.get("name") or node)
        if data.get("kind") == "ai_output":
            label = f"ai_output:{data.get('name') or 'model'}"
        if data.get("kind") == "sink" and data.get("role") == "model":
            label = str(data.get("ref") or label)
        labels.append(label)
    return labels


def _ai_out(file: str, scope: str, callee: str) -> str:
    return f"{file}::{scope}::ai_output:{callee}"


def _confidence(user_in: bool, output_used: bool) -> float:
    if user_in and output_used:
        return 0.95
    if user_in or output_used:
        return 0.85
    return 0.75
