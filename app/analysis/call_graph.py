"""Phase 6: basic inter-function CALLS graph from normalized Code Facts.

Not a compiler-quality resolver. Same-file, straightforward imports,
route handler → service, and service → AI SDK are enough.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable

import networkx as nx

from app.analysis.source_models import MODULE_SCOPE

EDGE_TYPE = "CALLS"
SAME_FILE = 0.96
IMPORTED = 0.94
SELF_METHOD = 0.93
EXTERNAL_AI = 0.88
EXTERNAL = 0.75
UNRESOLVED = 0.30

_MAX_PUBLIC_EDGES = 80

_BUILTINS = frozenset(
    {
        "print",
        "len",
        "str",
        "int",
        "float",
        "bool",
        "list",
        "dict",
        "set",
        "tuple",
        "range",
        "enumerate",
        "zip",
        "map",
        "filter",
        "sorted",
        "reversed",
        "min",
        "max",
        "sum",
        "abs",
        "open",
        "type",
        "isinstance",
        "issubclass",
        "super",
        "property",
        "staticmethod",
        "classmethod",
        "Exception",
        "ValueError",
        "TypeError",
        "KeyError",
        "console",
        "console.log",
        "console.error",
        "console.warn",
        "JSON.parse",
        "JSON.stringify",
        "Object.keys",
        "Object.values",
        "Array.from",
        "parseInt",
        "parseFloat",
        "Number",
        "String",
        "Boolean",
        "Promise",
        "document",
        "window",
    }
)
_AI_HINTS = (
    "openai",
    "anthropic",
    "langchain",
    "langgraph",
    "litellm",
    "groq",
    "vertexai",
    "genai",
    "responses.create",
    "chat.completions",
    "completions.create",
    "generate_content",
    "generative",
    "mistral",
    "cohere",
    "huggingface",
)
_SOURCE_EXTS = (".py", ".tsx", ".ts", ".jsx", ".mjs", ".cjs", ".js")


def file_to_module(path: str) -> str:
    norm = path.replace("\\", "/").lstrip("./")
    lower = norm.lower()
    if lower.endswith(".d.ts"):
        norm = norm[:-5]
    else:
        for ext in _SOURCE_EXTS:
            if lower.endswith(ext):
                norm = norm[: -len(ext)]
                break
    parts = [part for part in norm.split("/") if part and part != "__init__"]
    return ".".join(parts)


def function_node_id(file: str, name: str, scope: str | None = None) -> str:
    module = file_to_module(file)
    if scope and scope not in {MODULE_SCOPE, "", name}:
        return f"{module}.{scope}.{name}"
    return f"{module}.{name}"


@dataclass
class CallGraph:
    """Directed CALLS graph. Later analyzers should use these helpers, not AST."""

    graph: nx.DiGraph = field(default_factory=nx.DiGraph)
    _name_index: dict[str, list[str]] = field(default_factory=dict)

    def resolve(self, token: str) -> str | None:
        if not token:
            return None
        if self.graph.has_node(token):
            return token
        matches = self._name_index.get(token) or []
        defined = [
            node
            for node in matches
            if self.graph.nodes[node].get("kind") in {"function", "method", "route_handler"}
        ]
        pool = defined or matches
        if len(pool) == 1:
            return pool[0]
        suffix = [node for node in self.graph.nodes if node == token or node.endswith(f".{token}")]
        defined_suffix = [
            node
            for node in suffix
            if self.graph.nodes[node].get("kind") in {"function", "method", "route_handler"}
        ]
        pool = defined_suffix or suffix
        if len(pool) == 1:
            return pool[0]
        return None

    def direct_callees(self, source: str) -> list[str]:
        node = self.resolve(source)
        if not node:
            return []
        return list(self.graph.successors(node))

    def direct_callers(self, target: str) -> list[str]:
        node = self.resolve(target)
        if not node:
            return []
        return list(self.graph.predecessors(node))

    def path_exists(self, source: str, target: str) -> bool:
        src = self.resolve(source)
        dst = self.resolve(target)
        if not src or not dst:
            return False
        return nx.has_path(self.graph, src, dst)

    def find_paths(self, source: str, target: str, max_depth: int = 8) -> list[list[str]]:
        src = self.resolve(source)
        dst = self.resolve(target)
        if not src or not dst:
            return []
        try:
            return [list(path) for path in nx.all_simple_paths(self.graph, src, dst, cutoff=max_depth)]
        except nx.NetworkXError:
            return []

    def edges(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for source, target, data in self.graph.edges(data=True):
            out.append(
                {
                    "source": source,
                    "target": target,
                    "type": data.get("type") or EDGE_TYPE,
                    "confidence": data.get("confidence"),
                }
            )
        return out

    def to_public_dict(self) -> dict[str, Any]:
        edges = self.edges()
        return {
            "node_count": self.graph.number_of_nodes(),
            "edge_count": self.graph.number_of_edges(),
            "edges": edges[:_MAX_PUBLIC_EDGES],
            "truncated": len(edges) > _MAX_PUBLIC_EDGES,
        }

    def _index(self, node_id: str, name: str) -> None:
        self._name_index.setdefault(name, [])
        if node_id not in self._name_index[name]:
            self._name_index[name].append(node_id)

    def add_node(self, node_id: str, **attrs: Any) -> None:
        if not self.graph.has_node(node_id):
            self.graph.add_node(node_id, **attrs)
        else:
            self.graph.nodes[node_id].update({k: v for k, v in attrs.items() if v is not None})
        name = attrs.get("name") or node_id.rsplit(".", 1)[-1]
        self._index(node_id, str(name))


def build_call_graph(source_facts: list[dict[str, Any]]) -> CallGraph:
    """Build a CALLS graph from Phase 3 facts only (no AST / Tree-sitter)."""
    graph = CallGraph()
    functions = [f for f in source_facts if f.get("fact_type") == "function"]
    calls = [f for f in source_facts if f.get("fact_type") == "function_call"]
    imports = [f for f in source_facts if f.get("fact_type") == "import"]
    routes = [f for f in source_facts if f.get("fact_type") == "route"]

    known_files = {str(f.get("file") or "") for f in functions if f.get("file")}
    by_file_name: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    func_ids: dict[tuple[str, str, str], str] = {}

    handler_keys = {
        (str(r.get("file") or ""), str(r.get("handler") or ""))
        for r in routes
        if r.get("handler")
    }

    for fn in functions:
        file = str(fn.get("file") or "")
        name = str(fn.get("name") or "")
        if not file or not name or name == "<anonymous>":
            continue
        scope = str(fn.get("scope") or MODULE_SCOPE)
        node_id = function_node_id(file, name, scope)
        kind = "method" if scope not in {MODULE_SCOPE, "", name} else "function"
        if (file, name) in handler_keys:
            kind = "route_handler"
        graph.add_node(
            node_id,
            name=name,
            kind=kind,
            file=file,
            line=fn.get("line"),
            language=fn.get("language"),
            scope=scope,
        )
        by_file_name[(file, name)].append(fn)
        func_ids[(file, scope, name)] = node_id

    imports_by_file: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in imports:
        file = str(item.get("file") or "")
        if file:
            imports_by_file[file].append(item)

    params_by_caller: dict[tuple[str, str], set[str]] = defaultdict(set)
    for fn in functions:
        file = str(fn.get("file") or "")
        name = str(fn.get("name") or "")
        params = {
            p.lstrip("*").lstrip(".")
            for p in (fn.get("parameters") or [])
            if p and p not in {"self", "cls", "this"}
        }
        params_by_caller[(file, name)] |= params

    for call in calls:
        file = str(call.get("file") or "")
        scope = str(call.get("scope") or "")
        callee = str(call.get("callee") or "").strip()
        if not file or not callee or scope in {MODULE_SCOPE, ""}:
            continue
        if callee in _BUILTINS or callee.split(".")[0] in {"console", "JSON", "Object", "Math"}:
            continue

        caller_id = _caller_id(file, scope, func_ids, by_file_name)
        if not caller_id:
            continue
        if not graph.graph.has_node(caller_id):
            graph.add_node(caller_id, name=scope, kind="function", file=file)

        target_id, confidence, kind = _resolve_callee(
            callee,
            file=file,
            scope=scope,
            caller_params=params_by_caller.get((file, scope), set()),
            by_file_name=by_file_name,
            func_ids=func_ids,
            imports=imports_by_file.get(file) or [],
            known_files=known_files,
        )
        if not target_id:
            continue
        if not graph.graph.has_node(target_id):
            name = target_id.rsplit(".", 1)[-1]
            if target_id.startswith("unresolved:"):
                name = target_id.split(":", 1)[-1]
            graph.add_node(
                target_id,
                name=name,
                kind=kind,
                file=None if kind in {"external", "unresolved"} else file,
            )
        if graph.graph.has_edge(caller_id, target_id):
            prev = graph.graph.edges[caller_id, target_id].get("confidence") or 0
            if confidence > prev:
                graph.graph.edges[caller_id, target_id]["confidence"] = confidence
            continue
        graph.graph.add_edge(
            caller_id,
            target_id,
            type=EDGE_TYPE,
            confidence=confidence,
            callee=callee,
            file=file,
            line=call.get("line"),
        )

    return graph


def _caller_id(
    file: str,
    scope: str,
    func_ids: dict[tuple[str, str, str], str],
    by_file_name: dict[tuple[str, str], list[dict[str, Any]]],
) -> str | None:
    for (f, _enclosing, name), node_id in func_ids.items():
        if f == file and name == scope:
            return node_id
    matches = by_file_name.get((file, scope)) or []
    if len(matches) == 1:
        fn = matches[0]
        return function_node_id(file, scope, str(fn.get("scope") or MODULE_SCOPE))
    return None


def _resolve_callee(
    callee: str,
    *,
    file: str,
    scope: str,
    caller_params: set[str],
    by_file_name: dict[tuple[str, str], list[dict[str, Any]]],
    func_ids: dict[tuple[str, str, str], str],
    imports: list[dict[str, Any]],
    known_files: set[str],
) -> tuple[str | None, float, str]:
    del scope
    simple = callee.split(".")[0]
    if callee in caller_params or (
        simple in caller_params and "." not in callee and not callee.startswith(("self.", "this."))
    ):
        return f"unresolved:{callee}", UNRESOLVED, "unresolved"

    if callee.startswith("self.") or callee.startswith("this."):
        method = callee.split(".", 1)[1].split(".")[0]
        node_id = _same_file_function(file, method, func_ids, by_file_name)
        if node_id:
            return node_id, SELF_METHOD, "method"

    if "." not in callee:
        same = _same_file_function(file, callee, func_ids, by_file_name)
        if same:
            return same, SAME_FILE, "function"
        imported = _imported_function(callee, file, imports, known_files, func_ids, by_file_name)
        if imported:
            return imported
        return f"unresolved:{callee}", UNRESOLVED, "unresolved"

    head, rest = callee.split(".", 1)
    imported = _imported_attr(head, rest, file, imports, known_files, func_ids, by_file_name)
    if imported:
        return imported

    confidence = EXTERNAL_AI if _looks_like_ai(callee) else EXTERNAL
    return callee, confidence, "external"


def _same_file_function(
    file: str,
    name: str,
    func_ids: dict[tuple[str, str, str], str],
    by_file_name: dict[tuple[str, str], list[dict[str, Any]]],
) -> str | None:
    matches = by_file_name.get((file, name)) or []
    if not matches:
        return None
    fn = matches[0]
    scope = str(fn.get("scope") or MODULE_SCOPE)
    return func_ids.get((file, scope, name)) or function_node_id(file, name, scope)


def _imported_function(
    name: str,
    file: str,
    imports: list[dict[str, Any]],
    known_files: set[str],
    func_ids: dict[tuple[str, str, str], str],
    by_file_name: dict[tuple[str, str], list[dict[str, Any]]],
) -> tuple[str, float, str] | None:
    for item in imports:
        local_names = _local_import_names(item)
        if name not in local_names:
            continue
        module = str(item.get("module") or "")
        target_file = _resolve_module_file(module, file, known_files)
        if target_file:
            node_id = _same_file_function(target_file, name, func_ids, by_file_name)
            if node_id:
                return node_id, IMPORTED, "function"
        if module:
            callee = f"{module}.{name}"
            return callee, EXTERNAL_AI if _looks_like_ai(callee) else EXTERNAL, "external"
    return None


def _imported_attr(
    head: str,
    rest: str,
    file: str,
    imports: list[dict[str, Any]],
    known_files: set[str],
    func_ids: dict[tuple[str, str, str], str],
    by_file_name: dict[tuple[str, str], list[dict[str, Any]]],
) -> tuple[str, float, str] | None:
    for item in imports:
        namespace = _namespace_name(item)
        module = str(item.get("module") or "")
        if namespace != head and not (
            not item.get("is_from") and (item.get("alias") or module.split(".")[-1]) == head
        ):
            continue
        target_file = _resolve_module_file(module, file, known_files)
        fn_name = rest.split(".")[0]
        if target_file:
            node_id = _same_file_function(target_file, fn_name, func_ids, by_file_name)
            if node_id:
                return node_id, IMPORTED, "function"
        if module:
            callee = f"{module}.{rest}"
            return callee, EXTERNAL_AI if _looks_like_ai(callee) else EXTERNAL, "external"
    return None


def _local_import_names(item: dict[str, Any]) -> set[str]:
    names = {str(n) for n in (item.get("names") or []) if n and n != "*"}
    alias = item.get("alias")
    if alias and item.get("is_from"):
        names.add(str(alias))
    return names


def _namespace_name(item: dict[str, Any]) -> str | None:
    if item.get("alias") and (not item.get("is_from") or "*" in (item.get("names") or [])):
        return str(item.get("alias"))
    if not item.get("is_from"):
        module = str(item.get("module") or "")
        return module.split(".")[-1] if module else None
    if "*" in (item.get("names") or []) and item.get("alias"):
        return str(item.get("alias"))
    return None


def _resolve_module_file(module: str, from_file: str, known_files: set[str]) -> str | None:
    if not module:
        return None
    for path in _module_candidates(module, from_file):
        if path in known_files:
            return path
    return None


def _module_candidates(module: str, from_file: str) -> Iterable[str]:
    base_dir = from_file.replace("\\", "/").rsplit("/", 1)[0] if "/" in from_file.replace("\\", "/") else ""
    if module.startswith("."):
        dots = len(module) - len(module.lstrip("."))
        rest = module.lstrip(".").replace(".", "/")
        root = base_dir
        for _ in range(max(0, dots - 1)):
            root = root.rsplit("/", 1)[0] if "/" in root else ""
        rel = f"{root}/{rest}" if rest else root
        yield from _with_source_exts(rel.strip("/"))
        return
    if module.startswith("./") or module.startswith("../"):
        rel = _normpath(f"{base_dir}/{module}" if base_dir else module)
        yield from _with_source_exts(rel)
        return
    yield from _with_source_exts(module.replace(".", "/"))


def _with_source_exts(stem: str) -> list[str]:
    stem = stem.strip("/")
    if not stem:
        return []
    out = [f"{stem}{ext}" for ext in _SOURCE_EXTS]
    out.extend(
        [
            f"{stem}/__init__.py",
            f"{stem}/index.ts",
            f"{stem}/index.js",
            f"{stem}/index.tsx",
        ]
    )
    return out


def _normpath(path: str) -> str:
    parts: list[str] = []
    for part in path.replace("\\", "/").split("/"):
        if not part or part == ".":
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


def _looks_like_ai(callee: str) -> bool:
    lower = callee.lower()
    return any(hint in lower for hint in _AI_HINTS)
