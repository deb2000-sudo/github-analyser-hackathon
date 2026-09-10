"""Phase 7: simple value flow from normalized Code Facts.

Tracks assignments, aliases, concatenations, templates, arguments, returns,
and basic dict/object construction. Not compiler-grade taint analysis.
Never invents a flow; unprovable paths are UNKNOWN.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal

import networkx as nx

from app.analysis.source_models import MODULE_SCOPE

FlowAnswer = bool | Literal["unknown"]
EDGE_TYPE = "FLOWS_TO"

_MAX_PUBLIC_EDGES = 80
_KEYWORDS = frozenset(
    {
        "True",
        "False",
        "None",
        "true",
        "false",
        "null",
        "undefined",
        "and",
        "or",
        "not",
        "in",
        "is",
        "if",
        "else",
        "return",
        "new",
        "const",
        "let",
        "var",
        "function",
        "class",
        "self",
        "this",
        "await",
        "async",
    }
)
_IDENT = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")
_FEXPR = re.compile(r"\{([A-Za-z_][A-Za-z0-9_.]*)\}")
_TPL = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_.]*)\}")


@dataclass(frozen=True)
class DataFlowConfig:
    """Configurable SOURCE roots and SINK callees. No AI-specific sinks here."""

    source_roots: tuple[str, ...] = (
        "request",
        "body",
        "req",
        "payload",
        "form",
        "upload",
        "file",
        "files",
        "data",
    )
    source_prefixes: tuple[str, ...] = (
        "event.target",
        "e.target",
        "formData",
    )
    sink_callees: tuple[str, ...] = ("send",)
    dynamic_callees: tuple[str, ...] = (
        "getattr",
        "setattr",
        "eval",
        "exec",
        "Function",
        "__import__",
    )


@dataclass
class DataFlowGraph:
    """Directed FLOWS_TO graph of values. Later analyzers consume this, not AST."""

    graph: nx.DiGraph = field(default_factory=nx.DiGraph)
    config: DataFlowConfig = field(default_factory=DataFlowConfig)
    _ref_index: dict[str, list[str]] = field(default_factory=dict)

    def path_exists(self, source: str, sink: str) -> FlowAnswer:
        """Proven flow True, proven absence False, otherwise unknown."""
        if source in {"*", "source", "any"}:
            sources = [n for n, data in self.graph.nodes(data=True) if data.get("kind") == "source"]
        else:
            sources = self._lookup(source, kinds={"source", "value"})
        sinks = self._lookup(sink, kinds={"sink", "unknown", "value"})
        if not sources or not sinks:
            return False
        proven = False
        unknown = False
        for src in sources:
            for dst in sinks:
                if not nx.has_path(self.graph, src, dst):
                    continue
                kind = self.graph.nodes[dst].get("kind")
                if kind == "unknown":
                    unknown = True
                else:
                    proven = True
        if proven:
            return True
        if unknown:
            return "unknown"
        return False

    def find_paths(self, source: str, sink: str, max_depth: int = 10) -> list[list[str]]:
        sources = self._lookup(source, kinds={"source", "value"})
        sinks = self._lookup(sink, kinds={"sink", "unknown", "value"})
        paths: list[list[str]] = []
        for src in sources:
            for dst in sinks:
                try:
                    paths.extend(
                        list(p) for p in nx.all_simple_paths(self.graph, src, dst, cutoff=max_depth)
                    )
                except nx.NetworkXError:
                    continue
        return paths

    def edges(self) -> list[dict[str, Any]]:
        return [
            {
                "source": src,
                "target": dst,
                "type": data.get("type") or EDGE_TYPE,
                "confidence": data.get("confidence", 0.9),
            }
            for src, dst, data in self.graph.edges(data=True)
        ]

    def to_public_dict(self) -> dict[str, Any]:
        edges = self.edges()
        return {
            "node_count": self.graph.number_of_nodes(),
            "edge_count": self.graph.number_of_edges(),
            "source_count": sum(1 for _, d in self.graph.nodes(data=True) if d.get("kind") == "source"),
            "sink_count": sum(1 for _, d in self.graph.nodes(data=True) if d.get("kind") == "sink"),
            "edges": edges[:_MAX_PUBLIC_EDGES],
            "truncated": len(edges) > _MAX_PUBLIC_EDGES,
        }

    def add_node(self, node_id: str, **attrs: Any) -> None:
        if not self.graph.has_node(node_id):
            self.graph.add_node(node_id, **attrs)
        else:
            self.graph.nodes[node_id].update({k: v for k, v in attrs.items() if v is not None})
        ref = str(attrs.get("ref") or attrs.get("name") or "")
        if ref:
            self._ref_index.setdefault(ref, [])
            if node_id not in self._ref_index[ref]:
                self._ref_index[ref].append(node_id)

    def add_flow(self, src: str, dst: str, *, confidence: float = 0.9) -> None:
        if src == dst or not src or not dst:
            return
        if self.graph.has_edge(src, dst):
            prev = float(self.graph.edges[src, dst].get("confidence") or 0)
            if confidence > prev:
                self.graph.edges[src, dst]["confidence"] = confidence
            return
        self.graph.add_edge(src, dst, type=EDGE_TYPE, confidence=confidence)

    def _lookup(self, token: str, *, kinds: set[str]) -> list[str]:
        if not token:
            return []
        if self.graph.has_node(token) and self.graph.nodes[token].get("kind") in kinds:
            return [token]
        hits = []
        for node_id in self._ref_index.get(token, []):
            if self.graph.nodes[node_id].get("kind") in kinds:
                hits.append(node_id)
        if hits:
            return hits
        for node_id, data in self.graph.nodes(data=True):
            if data.get("kind") not in kinds:
                continue
            if data.get("name") == token or data.get("ref") == token:
                hits.append(node_id)
        return hits


def build_data_flow(
    source_facts: list[dict[str, Any]],
    *,
    config: DataFlowConfig | None = None,
) -> DataFlowGraph:
    cfg = config or DataFlowConfig()
    dfg = DataFlowGraph(config=cfg)

    functions = [f for f in source_facts if f.get("fact_type") == "function"]
    assigns = [f for f in source_facts if f.get("fact_type") == "assignment"]
    calls = [f for f in source_facts if f.get("fact_type") == "function_call"]
    returns = [f for f in source_facts if f.get("fact_type") == "return"]
    routes = [f for f in source_facts if f.get("fact_type") == "route"]

    handlers = {(str(r.get("file") or ""), str(r.get("handler") or "")) for r in routes if r.get("handler")}
    params_by_fn: dict[tuple[str, str], list[str]] = {}
    for fn in functions:
        file = str(fn.get("file") or "")
        name = str(fn.get("name") or "")
        params = [p for p in (fn.get("parameters") or []) if p and p not in {"self", "cls", "this"}]
        params = [p.lstrip("*").lstrip(".") for p in params]
        params_by_fn[(file, name)] = params
        scope = name
        for param in params:
            kind = "source" if _is_source_ref(param, cfg, params) or (file, name) in handlers else "value"
            if _is_source_ref(param, cfg, params):
                kind = "source"
            elif (file, name) in handlers and param not in {"self", "cls"}:
                kind = "source"
            dfg.add_node(_val(file, scope, param), kind=kind, name=param, ref=param, file=file, scope=scope)

    symbols: dict[tuple[str, str], set[str]] = {}
    for (file, name), params in params_by_fn.items():
        symbols[(file, name)] = set(params)
    for item in assigns:
        file = str(item.get("file") or "")
        scope = str(item.get("scope") or MODULE_SCOPE)
        target = str(item.get("target") or "")
        if target:
            symbols.setdefault((file, scope), set()).add(target.split(".")[0])

    dynamic_names: set[tuple[str, str, str]] = set()
    for item in assigns:
        file = str(item.get("file") or "")
        scope = str(item.get("scope") or MODULE_SCOPE)
        target = str(item.get("target") or "")
        value = str(item.get("value") or "")
        if target and _is_dynamic_expr(value, cfg):
            dynamic_names.add((file, scope, target))

    for item in assigns:
        file = str(item.get("file") or "")
        scope = str(item.get("scope") or MODULE_SCOPE)
        if not scope:
            continue
        target = str(item.get("target") or "")
        value = item.get("value")
        if not target:
            continue
        matched = _matching_call(item, calls)
        if matched:
            _propagate_call(dfg, matched, params_by_fn, symbols, cfg, dynamic_names, assign_target=target)
            continue
        target_id = _ensure_value(dfg, file, scope, target, cfg, params_by_fn.get((file, scope), []))
        for ref in extract_refs(value):
            if not _usable_ref(ref, file, scope, symbols, cfg):
                continue
            src_id = _ensure_value(dfg, file, scope, ref, cfg, params_by_fn.get((file, scope), []))
            dfg.add_flow(src_id, target_id)

    for call in calls:
        file = str(call.get("file") or "")
        scope = str(call.get("scope") or MODULE_SCOPE)
        if not scope:
            continue
        if _matching_assign(call, assigns):
            continue
        _propagate_call(dfg, call, params_by_fn, symbols, cfg, dynamic_names, assign_target=None)

    for item in returns:
        file = str(item.get("file") or "")
        scope = str(item.get("scope") or MODULE_SCOPE)
        if not scope:
            continue
        ret_id = _ret(file, scope)
        dfg.add_node(ret_id, kind="value", name="<return>", ref="<return>", file=file, scope=scope)
        matched = _matching_call_for_return(item, calls)
        if matched:
            _propagate_call(dfg, matched, params_by_fn, symbols, cfg, dynamic_names, assign_target=None)
            callee = str(matched.get("callee") or "")
            callee_fn = _find_function(callee, file, params_by_fn)
            if callee_fn:
                dfg.add_flow(_ret(callee_fn[0], callee_fn[1]), ret_id)
        for ref in extract_refs(item.get("value")):
            if not _usable_ref(ref, file, scope, symbols, cfg):
                continue
            src_id = _ensure_value(dfg, file, scope, ref, cfg, params_by_fn.get((file, scope), []))
            dfg.add_flow(src_id, ret_id)

    return dfg


def extract_refs(value: str | None) -> list[str]:
    if not value:
        return []
    text = str(value).strip()
    found: list[str] = []
    found.extend(_FEXPR.findall(text))
    found.extend(_TPL.findall(text))
    stripped = _strip_string_literals(text)
    for match in _IDENT.finditer(stripped):
        token = match.group(0)
        if token not in _KEYWORDS:
            found.append(token)
    out: list[str] = []
    seen: set[str] = set()
    for token in found:
        if token not in seen:
            seen.add(token)
            out.append(token)
    return out


def _strip_string_literals(text: str) -> str:
    """Replace quoted / template literals with spaces so identifiers inside them are ignored."""
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        if text.startswith(("f'", 'f"', "F'", 'F"'), i):
            i += 1
            continue
        triple = text[i : i + 3]
        if triple in {'"""', "'''"}:
            end = text.find(triple, i + 3)
            i = n if end < 0 else end + 3
            out.append(" ")
            continue
        if text[i] in {'"', "'", "`"}:
            quote = text[i]
            i += 1
            while i < n:
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == quote:
                    i += 1
                    break
                i += 1
            out.append(" ")
            continue
        out.append(text[i])
        i += 1
    return "".join(out)


def _val(file: str, scope: str, name: str) -> str:
    return f"{file}::{scope}::{name}"


def _ret(file: str, func: str) -> str:
    return f"{file}::{func}::<return>"


def _sink_id(file: str, scope: str, callee: str) -> str:
    return f"{file}::{scope}::sink:{callee}"


def _ensure_value(
    dfg: DataFlowGraph,
    file: str,
    scope: str,
    ref: str,
    cfg: DataFlowConfig,
    params: list[str],
) -> str:
    node_id = _val(file, scope, ref)
    kind = "source" if _is_source_ref(ref, cfg, params) else "value"
    dfg.add_node(node_id, kind=kind, name=ref.split(".")[-1], ref=ref, file=file, scope=scope)
    root = ref.split(".")[0]
    if "." in ref and root:
        root_id = _val(file, scope, root)
        if not dfg.graph.has_node(root_id):
            root_kind = "source" if _is_source_ref(root, cfg, params) else "value"
            dfg.add_node(root_id, kind=root_kind, name=root, ref=root, file=file, scope=scope)
        dfg.add_flow(root_id, node_id, confidence=0.92)
    return node_id


def _is_source_ref(ref: str, cfg: DataFlowConfig, params: Iterable[str] | None = None) -> bool:
    params = set(params or [])
    if any(ref == prefix or ref.startswith(prefix + ".") for prefix in cfg.source_prefixes):
        return True
    root = ref.split(".")[0]
    if root in cfg.source_roots:
        return True
    if root in params and root in cfg.source_roots:
        return True
    return False


def _usable_ref(
    ref: str,
    file: str,
    scope: str,
    symbols: dict[tuple[str, str], set[str]],
    cfg: DataFlowConfig,
) -> bool:
    root = ref.split(".")[0]
    if _is_source_ref(ref, cfg, symbols.get((file, scope), set())):
        return True
    return root in symbols.get((file, scope), set())


def _is_dynamic_expr(value: str, cfg: DataFlowConfig) -> bool:
    token = value.split("(")[0].split(".")[-1]
    return token in cfg.dynamic_callees or value in cfg.dynamic_callees


def _matching_call(assign: dict[str, Any], calls: list[dict[str, Any]]) -> dict[str, Any] | None:
    file = assign.get("file")
    scope = assign.get("scope")
    value = str(assign.get("value") or "")
    line = assign.get("line")
    candidates = [
        c
        for c in calls
        if c.get("file") == file and c.get("scope") == scope and str(c.get("callee") or "") == value
    ]
    if not candidates:
        return None
    same_line = [c for c in candidates if c.get("line") == line]
    return (same_line or candidates)[0]


def _matching_assign(call: dict[str, Any], assigns: list[dict[str, Any]]) -> dict[str, Any] | None:
    file = call.get("file")
    scope = call.get("scope")
    callee = str(call.get("callee") or "")
    line = call.get("line")
    candidates = [
        a
        for a in assigns
        if a.get("file") == file and a.get("scope") == scope and str(a.get("value") or "") == callee
    ]
    if not candidates:
        return None
    same_line = [a for a in candidates if a.get("line") == line]
    return (same_line or candidates)[0]


def _matching_call_for_return(ret: dict[str, Any], calls: list[dict[str, Any]]) -> dict[str, Any] | None:
    file = ret.get("file")
    scope = ret.get("scope")
    value = str(ret.get("value") or "")
    line = ret.get("line")
    candidates = [
        c
        for c in calls
        if c.get("file") == file and c.get("scope") == scope and str(c.get("callee") or "") == value
    ]
    if not candidates:
        return None
    same_line = [c for c in candidates if c.get("line") == line]
    return (same_line or candidates)[0]


def _find_function(
    name: str,
    from_file: str,
    params_by_fn: dict[tuple[str, str], list[str]],
) -> tuple[str, str] | None:
    if (from_file, name) in params_by_fn:
        return from_file, name
    hits = [(file, fn) for (file, fn) in params_by_fn if fn == name]
    if len(hits) == 1:
        return hits[0]
    return None


def _propagate_call(
    dfg: DataFlowGraph,
    call: dict[str, Any],
    params_by_fn: dict[tuple[str, str], list[str]],
    symbols: dict[tuple[str, str], set[str]],
    cfg: DataFlowConfig,
    dynamic_names: set[tuple[str, str, str]],
    *,
    assign_target: str | None,
) -> None:
    file = str(call.get("file") or "")
    scope = str(call.get("scope") or MODULE_SCOPE)
    callee = str(call.get("callee") or "")
    args = [str(a) for a in (call.get("arguments") or [])]
    params = params_by_fn.get((file, scope), [])

    is_dynamic = (
        callee in cfg.dynamic_callees
        or (file, scope, callee) in dynamic_names
    )
    callee_fn = None if is_dynamic else _find_function(callee, file, params_by_fn)

    for index, raw in enumerate(args):
        key, val = _split_kwarg(raw)
        refs = extract_refs(val)
        if not refs and (
            _usable_ref(val, file, scope, symbols, cfg) or _is_source_ref(val, cfg, params)
        ):
            refs = [val]
        for ref in refs:
            if not _usable_ref(ref, file, scope, symbols, cfg) and not _is_source_ref(ref, cfg, params):
                continue
            src_id = _ensure_value(dfg, file, scope, ref, cfg, params)
            if is_dynamic:
                sink_id = _sink_id(file, scope, callee)
                dfg.add_node(
                    sink_id,
                    kind="unknown",
                    name=callee,
                    ref=callee,
                    file=file,
                    scope=scope,
                )
                dfg.add_flow(src_id, sink_id, confidence=0.35)
                continue
            if callee_fn:
                dest_params = params_by_fn.get(callee_fn, [])
                dest_name = key if key in dest_params else (dest_params[index] if index < len(dest_params) else None)
                if dest_name:
                    dest_id = _ensure_value(dfg, callee_fn[0], callee_fn[1], dest_name, cfg, dest_params)
                    dfg.add_flow(src_id, dest_id)
            if callee in cfg.sink_callees:
                sink_id = _sink_id(file, scope, callee)
                dfg.add_node(sink_id, kind="sink", name=callee, ref=callee, file=file, scope=scope)
                dfg.add_flow(src_id, sink_id)

    if assign_target and callee_fn and not is_dynamic:
        target_id = _ensure_value(dfg, file, scope, assign_target, cfg, params)
        dfg.add_flow(_ret(callee_fn[0], callee_fn[1]), target_id)
        dfg.add_node(
            _ret(callee_fn[0], callee_fn[1]),
            kind="value",
            name="<return>",
            ref="<return>",
            file=callee_fn[0],
            scope=callee_fn[1],
        )


def _split_kwarg(raw: str) -> tuple[str | None, str]:
    if "=" in raw:
        key, _, val = raw.partition("=")
        if key.isidentifier():
            return key, val
    return None, raw
