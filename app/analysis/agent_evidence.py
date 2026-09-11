"""Phase 14: deterministic agent/orchestration evidence from Code Facts.

Naming or a langchain/langgraph/agent string in dependencies or README
is recorded but is not enough to call something an agent.
"""

from __future__ import annotations

import re
from typing import Any

from app.analysis.ai_evidence import list_model_invocations
from app.metrics.ai_packages import is_agent_framework

AGENT_CLASSES = (
    "NO_AGENT",
    "LLM_WRAPPER",
    "LINEAR_CHAIN",
    "TOOL_USING_LLM",
    "WORKFLOW_ORCHESTRATION",
    "GENUINE_AGENT_ORCHESTRATION",
)
ORCHESTRATION_CLASSES = frozenset({"WORKFLOW_ORCHESTRATION", "GENUINE_AGENT_ORCHESTRATION"})

_SYMBOL_RE = re.compile(
    r"(agent|tool|node|supervisor|worker|planner|executor|orchestrat|handoff|workflow)",
    re.I,
)
_GRAPH_NODE_CALLEES = (
    "add_node",
    "addNode",
    "add_nodes",
)
_GRAPH_EDGE_CALLEES = (
    "add_edge",
    "addEdge",
    "add_conditional_edges",
    "addConditionalEdges",
    "set_entry_point",
    "set_finish_point",
    "setEntryPoint",
)
_GRAPH_CTORS = (
    "StateGraph",
    "MessageGraph",
    "StateGraph[",
    "CompiledGraph",
)
_TOOL_REG_CALLEES = (
    "bind_tools",
    "bindTools",
    "add_tool",
    "add_tools",
    "register_tool",
    "create_react_agent",
    "initialize_agent",
    "AgentExecutor",
    "StructuredTool.from_function",
    "tool",
)
_TOOL_CALL_CALLEES = (
    "tool_call",
    "tool_calls",
    "invoke_tool",
    "call_tool",
    "run_tool",
)
_CHAIN_CALLEES = (
    "LLMChain",
    "SequentialChain",
    "SimpleSequentialChain",
    "pipe",
)
_STATE_CALLEES = (
    "update_state",
    "get_state",
    "goto",
)
_LOOP_HINTS = re.compile(
    r"(should_continue|route_node|agent_loop|orchestration_loop|next_node|conditional)",
    re.I,
)
_README_NAME = re.compile(r"(^|/)readme(\.[a-z0-9]+)?$", re.I)


def collect_agent_evidence(
    source_facts: list[dict[str, Any]] | None,
    *,
    manifests: dict[str, str] | None = None,
    parsed_manifests: dict[str, Any] | None = None,
    call_graph: Any | None = None,
    file_contents: dict[str, str] | None = None,
    ai_usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Collect static agent signals. Does not classify and does not call Gemini."""
    facts = list(source_facts or [])
    items: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    def add(kind: str, *, file: str = "", line: Any = None, detail: str, extra: dict[str, Any] | None = None) -> None:
        eid = _evidence_id(kind, file, line, detail)
        if eid in seen_ids:
            return
        seen_ids.add(eid)
        row = {"id": eid, "kind": kind, "file": file or None, "line": line, "detail": detail}
        if extra:
            row.update(extra)
        items.append(row)

    imported = _imported_frameworks(facts, add)
    declared = _declared_frameworks(manifests, parsed_manifests, ai_usage, add)
    symbols = _semantic_symbols(facts, add)
    tool_regs = _tool_registrations(facts, add)
    invocations = list_model_invocations(facts)
    for inv in invocations:
        add(
            "INVOKE",
            file=str(inv.get("file") or ""),
            line=inv.get("line"),
            detail=str(inv.get("callee") or "model"),
            extra={"providers": inv.get("providers") or []},
        )
    nodes = _graph_nodes(facts, add)
    edges = _graph_edges(facts, add)
    states = _state_transitions(facts, add)
    tool_calls = _tool_calls(facts, add)
    loops = _orchestration_loops(facts, add)
    chain_hits = _linear_chain(facts, add)
    call_edges = _call_graph_slice(call_graph, items)

    readme_mentions = _readme_mentions(file_contents)
    for name in readme_mentions:
        add("README", file=_readme_path(file_contents), line=1, detail=f"README mentions {name}")

    needs_semantic = bool(
        imported or tool_regs or nodes or edges or invocations or loops or tool_calls or chain_hits
    )
    return {
        "items": items[:80],
        "framework_dependencies": {
            "imported": imported,
            "declared": declared,
            "readme_mentions": readme_mentions,
        },
        "semantic_symbols": symbols,
        "tool_registrations": tool_regs,
        "model_invocations": invocations[:20],
        "graph_nodes": nodes,
        "graph_edges": edges,
        "state_transitions": states,
        "call_graph": call_edges,
        "ai_calls": [
            {"file": i.get("file"), "line": i.get("line"), "callee": i.get("callee")}
            for i in invocations[:20]
        ],
        "tool_calls": tool_calls,
        "orchestration_loops": loops,
        "needs_semantic": needs_semantic,
        "naming_or_manifest_only": bool((declared or readme_mentions or symbols) and not needs_semantic),
        "note": (
            "A langchain/langgraph/agent string in dependencies or README is not "
            "sufficient to classify this as an agent."
        ),
    }


def evidence_needs_semantic(evidence: dict[str, Any] | None) -> bool:
    return bool((evidence or {}).get("needs_semantic"))


def normalize_agent_judgment(section: dict[str, Any] | None, evidence: dict[str, Any]) -> dict[str, Any]:
    """Map Gemini output onto a classification that must cite evidence IDs."""
    section = section or {}
    raw = str(section.get("classification") or section.get("agent_classification") or "").upper()
    if raw not in AGENT_CLASSES:
        raw = "LLM_WRAPPER" if evidence.get("model_invocations") else "NO_AGENT"
    allowed = {str(item.get("id")) for item in evidence.get("items") or []}
    cited = [str(eid) for eid in (section.get("evidence_ids") or []) if str(eid) in allowed]
    if raw in ORCHESTRATION_CLASSES and not cited:
        raw = "TOOL_USING_LLM" if evidence.get("tool_registrations") or evidence.get("tool_calls") else (
            "LLM_WRAPPER" if evidence.get("model_invocations") else "NO_AGENT"
        )
    if evidence.get("naming_or_manifest_only") and raw in ORCHESTRATION_CLASSES:
        raw = "NO_AGENT"
        cited = [item["id"] for item in (evidence.get("items") or []) if str(item.get("kind")) in {"FRAMEWORK", "README", "SYMBOL"}][:6]
    agents = section.get("agents") or []
    return {
        "classification": raw,
        "agent_count": int(section.get("agent_count") or len(agents)),
        "agents": agents,
        "has_real_orchestration": raw in ORCHESTRATION_CLASSES,
        "confidence": section.get("confidence") or ("high" if raw == "NO_AGENT" else "medium"),
        "reasoning": section.get("reasoning") or f"Classified as {raw}.",
        "evidence_ids": cited,
        "status": "ok",
    }


def _evidence_id(kind: str, file: str, line: Any, detail: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", detail)[:40].strip("_") or "item"
    loc = file.replace("\\", "/") if file else "unknown"
    suffix = f":{line}" if line not in (None, "") else ""
    return f"AGT.{kind}:{loc}{suffix}:{slug}"


def _leaf(callee: str) -> str:
    return str(callee or "").split(".")[-1]


def _imported_frameworks(facts: list[dict[str, Any]], add) -> list[str]:
    found: list[str] = []
    for item in facts:
        if item.get("fact_type") != "import":
            continue
        module = str(item.get("module") or "")
        root = module.split(".")[0].replace("_", "-")
        candidates = [module.lower(), root.lower(), root.replace("-", "_")]
        pkg = next((c for c in candidates if is_agent_framework(c)), None)
        if pkg is None and any(key in module.lower() for key in ("langgraph", "langchain", "crewai", "autogen", "llama_index", "llamaindex")):
            pkg = module.split(".")[0]
        if not pkg:
            continue
        name = pkg if is_agent_framework(pkg) else pkg
        if name not in found:
            found.append(name)
        add("FRAMEWORK", file=str(item.get("file") or ""), line=item.get("line"), detail=f"import {module}")
    return found


def _declared_frameworks(
    manifests: dict[str, str] | None,
    parsed_manifests: dict[str, Any] | None,
    ai_usage: dict[str, Any] | None,
    add,
) -> list[str]:
    names: list[str] = []
    for pkg in (ai_usage or {}).get("agent_frameworks_found") or []:
        if pkg not in names:
            names.append(str(pkg))
    parsed = parsed_manifests or {}
    for group in (parsed.get("npm") or [], parsed.get("python") or []):
        for pkg in group:
            key = str(pkg).split("==")[0].split("@")[0].strip().lower()
            if is_agent_framework(key) and key not in names:
                names.append(key)
        for path, content in (manifests or {}).items():
            if not isinstance(content, str):
                continue
            lower = content.lower()
            for token in ("langgraph", "langchain", "crewai", "autogen", "llama-index", "llamaindex"):
                if token in lower and token not in names:
                    names.append(token)
                    add("FRAMEWORK", file=path, line=1, detail=f"manifest declares {token}")
    for name in names:
        add("FRAMEWORK", file="<manifest>", line=1, detail=f"declared {name}")
    return names


def _semantic_symbols(facts: list[dict[str, Any]], add) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in facts:
        if item.get("fact_type") not in {"function", "class"}:
            continue
        name = str(item.get("name") or "")
        bases = " ".join(str(b) for b in (item.get("bases") or []))
        if not (_SYMBOL_RE.search(name) or _SYMBOL_RE.search(bases)):
            continue
        row = {
            "name": name,
            "kind": item.get("fact_type"),
            "file": item.get("file"),
            "line": item.get("line"),
            "bases": item.get("bases") or [],
        }
        rows.append(row)
        add("SYMBOL", file=str(item.get("file") or ""), line=item.get("line"), detail=name)
    return rows[:24]


def _tool_registrations(facts: list[dict[str, Any]], add) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in facts:
        if item.get("fact_type") == "function":
            decos = [str(d) for d in (item.get("decorators") or [])]
            if any(_leaf(d) == "tool" or d.endswith(".tool") for d in decos):
                row = {"name": item.get("name"), "file": item.get("file"), "line": item.get("line"), "via": "decorator"}
                rows.append(row)
                add("TOOL_REG", file=str(item.get("file") or ""), line=item.get("line"), detail=str(item.get("name")))
                continue
        if item.get("fact_type") != "function_call":
            continue
        callee = str(item.get("callee") or "")
        leaf = _leaf(callee)
        if leaf in _TOOL_REG_CALLEES or callee in _TOOL_REG_CALLEES:
            row = {"callee": callee, "file": item.get("file"), "line": item.get("line"), "arguments": item.get("arguments") or []}
            rows.append(row)
            add("TOOL_REG", file=str(item.get("file") or ""), line=item.get("line"), detail=callee)
    return rows[:20]


def _graph_nodes(facts: list[dict[str, Any]], add) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in facts:
        if item.get("fact_type") == "function_call":
            callee = str(item.get("callee") or "")
            if _leaf(callee) in _GRAPH_NODE_CALLEES:
                row = {"callee": callee, "file": item.get("file"), "line": item.get("line"), "arguments": item.get("arguments") or []}
                rows.append(row)
                add("NODE", file=str(item.get("file") or ""), line=item.get("line"), detail=callee)
        if item.get("fact_type") == "assignment":
            value = str(item.get("value") or "")
            if any(ctor in value for ctor in _GRAPH_CTORS):
                row = {"target": item.get("target"), "value": value, "file": item.get("file"), "line": item.get("line")}
                rows.append(row)
                add("NODE", file=str(item.get("file") or ""), line=item.get("line"), detail=value[:80])
    return rows[:20]


def _graph_edges(facts: list[dict[str, Any]], add) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in facts:
        if item.get("fact_type") != "function_call":
            continue
        callee = str(item.get("callee") or "")
        if _leaf(callee) in _GRAPH_EDGE_CALLEES:
            row = {"callee": callee, "file": item.get("file"), "line": item.get("line"), "arguments": item.get("arguments") or []}
            rows.append(row)
            add("EDGE", file=str(item.get("file") or ""), line=item.get("line"), detail=callee)
    return rows[:20]


def _state_transitions(facts: list[dict[str, Any]], add) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in facts:
        if item.get("fact_type") == "function_call":
            callee = str(item.get("callee") or "")
            if _leaf(callee) in _STATE_CALLEES:
                rows.append({"callee": callee, "file": item.get("file"), "line": item.get("line")})
                add("STATE", file=str(item.get("file") or ""), line=item.get("line"), detail=callee)
        if item.get("fact_type") == "class":
            name = str(item.get("name") or "")
            if name.endswith("State") or name.endswith("GraphState"):
                rows.append({"name": name, "file": item.get("file"), "line": item.get("line"), "kind": "state_type"})
                add("STATE", file=str(item.get("file") or ""), line=item.get("line"), detail=name)
    return rows[:16]


def _tool_calls(facts: list[dict[str, Any]], add) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in facts:
        if item.get("fact_type") != "function_call":
            continue
        callee = str(item.get("callee") or "")
        leaf = _leaf(callee)
        if leaf in _TOOL_CALL_CALLEES or "tool_call" in callee:
            rows.append({"callee": callee, "file": item.get("file"), "line": item.get("line")})
            add("TOOL_CALL", file=str(item.get("file") or ""), line=item.get("line"), detail=callee)
    return rows[:16]


def _orchestration_loops(facts: list[dict[str, Any]], add) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in facts:
        blob = " ".join(str(item.get(k) or "") for k in ("name", "callee", "value"))
        if not _LOOP_HINTS.search(blob):
            continue
        if item.get("fact_type") not in {"function", "function_call", "assignment"}:
            continue
        rows.append(
            {
                "file": item.get("file"),
                "line": item.get("line"),
                "detail": item.get("name") or item.get("callee") or item.get("value"),
            }
        )
        add(
            "LOOP",
            file=str(item.get("file") or ""),
            line=item.get("line"),
            detail=str(item.get("name") or item.get("callee") or "loop"),
        )
    return rows[:16]


def _linear_chain(facts: list[dict[str, Any]], add) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in facts:
        if item.get("fact_type") != "function_call":
            continue
        callee = str(item.get("callee") or "")
        leaf = _leaf(callee)
        if leaf in _CHAIN_CALLEES or (
            leaf in {"invoke", "ainvoke", "stream", "batch"} and "chain" in callee.lower()
        ):
            rows.append({"callee": callee, "file": item.get("file"), "line": item.get("line")})
            add("CHAIN", file=str(item.get("file") or ""), line=item.get("line"), detail=callee)
        if leaf == "invoke" and any(
            "chain" in str(c.get("name") or "").lower() or "chain" in str(c.get("target") or "").lower()
            for c in facts
            if c.get("file") == item.get("file")
        ):
            rows.append({"callee": callee, "file": item.get("file"), "line": item.get("line")})
            add("CHAIN", file=str(item.get("file") or ""), line=item.get("line"), detail=callee)
    return rows[:12]


def _call_graph_slice(call_graph: Any, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if call_graph is None or not hasattr(call_graph, "to_public_dict"):
        return []
    files = {str(item.get("file")) for item in items if item.get("file")}
    edges = []
    for edge in (call_graph.to_public_dict().get("edges") or []):
        src = str(edge.get("source") or "")
        dst = str(edge.get("target") or "")
        if any(part in src or part in dst for part in files) or _SYMBOL_RE.search(src + " " + dst):
            edges.append({"source": src, "target": dst, "confidence": edge.get("confidence")})
        if len(edges) >= 20:
            break
    return edges


def _readme_mentions(file_contents: dict[str, str] | None) -> list[str]:
    hits: list[str] = []
    for path, content in (file_contents or {}).items():
        if not _README_NAME.search(path.split("/")[-1]):
            continue
        lower = content.lower()
        for token in ("langgraph", "langchain", "agent", "crewai", "autogen"):
            if token in lower and token not in hits:
                hits.append(token)
    return hits


def _readme_path(file_contents: dict[str, str] | None) -> str:
    for path in (file_contents or {}):
        if _README_NAME.search(path.split("/")[-1]):
            return path
    return "README.md"
