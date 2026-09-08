"""Phase 4: backend HTTP routes and frontend HTTP clients from normalized Code Facts."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})
_ROUTE_METHODS = {m.lower() for m in HTTP_METHODS} | {"route", "all"}
_BACKEND_OBJECTS = frozenset({"app", "router", "api", "api_router", "blueprint", "bp"})
_CLIENT_ROOTS = frozenset({"axios", "ky"})
_FETCH_CALLEES = frozenset({"fetch", "window.fetch", "globalthis.fetch", "self.fetch"})
_METHOD_IN_OPTIONS = re.compile(
    r"method\s*[:=]\s*['\"]?(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)['\"]?",
    re.I,
)
_IDENT = re.compile(r"^[A-Za-z_$][\w$]*$")
_ABS_URL = re.compile(r"https?://[^\s\"'`]+", re.I)
_PATH_TOKEN = re.compile(r"/[A-Za-z0-9._~()@%!$&*+,;=:{}\[\]/-]*")
_TEMPLATE = re.compile(r"\$\{[^}]+\}")
_NEXT_APP_API = re.compile(r"(?:^|/)(?:src/)?app/api/(.+)/route\.(tsx?|jsx?)$", re.I)
_NEXT_PAGES_API = re.compile(r"(?:^|/)(?:src/)?pages/api/(.+?)\.(tsx?|jsx?)$", re.I)


def detect_http_io(source_facts: list[dict[str, Any]]) -> dict[str, Any]:
    """Normalize backend routes and frontend clients. Does not match them."""
    calls = [f for f in source_facts if f.get("fact_type") == "function_call"]
    routes: list[dict[str, Any]] = []
    clients: list[dict[str, Any]] = []
    seen_routes: set[tuple[Any, ...]] = set()
    seen_clients: set[tuple[Any, ...]] = set()

    for fact in source_facts:
        kind = fact.get("fact_type")
        if kind == "route" and _is_backend_route(fact):
            item = _normalize_route(fact)
            key = (item.get("method"), item.get("path"), item.get("file"), item.get("line"))
            if key not in seen_routes:
                seen_routes.add(key)
                routes.append(item)
        elif kind == "http_request" and _is_frontend_client(fact):
            item = _normalize_client(fact, calls)
            key = (item.get("method"), item.get("raw"), item.get("file"), item.get("line"))
            if key not in seen_clients:
                seen_clients.add(key)
                clients.append(item)
        elif kind == "function":
            item = _next_api_route(fact)
            if item:
                key = (item.get("method"), item.get("path"), item.get("file"), item.get("line"))
                if key not in seen_routes:
                    seen_routes.add(key)
                    routes.append(item)
        elif kind == "function_call" and _is_fetch_callee(str(fact.get("callee") or "")):
            if not any(
                c.get("file") == fact.get("file") and c.get("line") == fact.get("line")
                for c in source_facts
                if c.get("fact_type") == "http_request"
            ):
                synthetic = {
                    "callee": fact.get("callee"),
                    "method": "GET",
                    "url": (fact.get("arguments") or [None])[0],
                    "file": fact.get("file"),
                    "line": fact.get("line"),
                    "language": fact.get("language"),
                }
                item = _normalize_client(synthetic, calls)
                key = (item.get("method"), item.get("raw"), item.get("file"), item.get("line"))
                if key not in seen_clients:
                    seen_clients.add(key)
                    clients.append(item)

    return {"backend_routes": routes, "frontend_requests": clients}


def normalize_url(raw: str | None) -> dict[str, Any]:
    """Turn a URL argument into a path plus resolution flags."""
    original = raw
    if raw is None:
        return _unresolved(original)
    text = str(raw).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"', "`"}:
        text = text[1:-1]
    text = text.strip()
    if not text:
        return _unresolved(original)
    if _IDENT.fullmatch(text) or re.fullmatch(r"\$\{[^}]+\}", text):
        return _unresolved(original)

    dynamic_base = bool(_TEMPLATE.search(text)) or bool(
        re.search(r"""[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*\s*\+\s*['\"]/""", text)
    )
    path = _path_from_text(text)
    if path:
        return {
            "path": _canonical_path(path),
            "resolved": True,
            "dynamic": False,
            "dynamic_base": dynamic_base,
            "raw": original,
        }
    return _unresolved(original)


def _unresolved(raw: str | None) -> dict[str, Any]:
    return {
        "path": None,
        "resolved": False,
        "dynamic": True,
        "dynamic_base": False,
        "raw": raw,
    }


def _path_from_text(text: str) -> str | None:
    abs_match = _ABS_URL.search(text)
    if abs_match:
        parsed = urlparse(abs_match.group(0))
        if parsed.path:
            return parsed.path
    match = _PATH_TOKEN.search(text)
    if not match:
        return None
    path = match.group(0)
    path = re.sub(r"\$\{$", "", path)
    path = path.rstrip("`'\"")
    return path or None


def _canonical_path(path: str) -> str:
    path = path.split("?")[0].split("#")[0]
    if len(path) > 1:
        path = path.rstrip("/")
    return path or "/"


def _is_fetch_callee(callee: str) -> bool:
    lower = callee.lower()
    return lower in _FETCH_CALLEES or lower.endswith(".fetch")


def _is_frontend_client(fact: dict[str, Any]) -> bool:
    callee = str(fact.get("callee") or "")
    if _is_fetch_callee(callee):
        return True
    root = callee.split(".")[0].lower()
    last = callee.rsplit(".", 1)[-1].lower()
    return root in _CLIENT_ROOTS and last in _ROUTE_METHODS


def _is_backend_route(fact: dict[str, Any]) -> bool:
    callee = str(fact.get("callee") or "")
    if _is_frontend_client({"callee": callee}):
        return False
    parts = callee.split(".")
    if len(parts) < 2:
        return False
    obj, method = parts[-2], parts[-1].lower()
    if method not in _ROUTE_METHODS:
        return False
    obj_l = obj.lower()
    return obj_l in _BACKEND_OBJECTS or obj_l.endswith("router") or obj_l.endswith("_app")


def _normalize_route(fact: dict[str, Any]) -> dict[str, Any]:
    info = normalize_url(fact.get("path"))
    method = str(fact.get("method") or "GET").upper()
    if method == "ROUTE":
        method = "GET"
    if method == "ALL":
        method = "ALL"
    return {
        "type": "http_route",
        "method": method,
        "path": info["path"],
        "file": fact.get("file"),
        "line": fact.get("line"),
        "raw": fact.get("path"),
        "resolved": bool(info["path"]),
        "dynamic_base": info["dynamic_base"],
        "callee": fact.get("callee"),
    }


def _next_api_route(fact: dict[str, Any]) -> dict[str, Any] | None:
    name = str(fact.get("name") or "").upper()
    if name not in HTTP_METHODS:
        return None
    file = str(fact.get("file") or "")
    path = _next_path_from_file(file)
    if not path:
        return None
    return {
        "type": "http_route",
        "method": name,
        "path": path,
        "file": fact.get("file"),
        "line": fact.get("line"),
        "raw": path,
        "resolved": True,
        "dynamic_base": False,
        "callee": "next.route_handler",
    }


def _next_path_from_file(file: str) -> str | None:
    norm = file.replace("\\", "/")
    match = _NEXT_APP_API.search(norm)
    if match:
        return _canonical_path("/api/" + match.group(1))
    match = _NEXT_PAGES_API.search(norm)
    if match:
        rest = match.group(1)
        if rest.endswith("/index"):
            rest = rest[: -len("/index")]
        return _canonical_path("/api/" + rest)
    return None


def _normalize_client(fact: dict[str, Any], calls: list[dict[str, Any]]) -> dict[str, Any]:
    raw = fact.get("url")
    method = str(fact.get("method") or "GET").upper()
    callee = str(fact.get("callee") or "")
    if _is_fetch_callee(callee):
        method = _fetch_method(fact, calls) or method
    info = normalize_url(raw if isinstance(raw, str) else (str(raw) if raw is not None else None))
    return {
        "type": "http_client",
        "method": method if method in HTTP_METHODS else "GET",
        "path": info["path"],
        "file": fact.get("file"),
        "line": fact.get("line"),
        "raw": raw,
        "resolved": info["resolved"],
        "dynamic": info["dynamic"],
        "dynamic_base": info["dynamic_base"],
        "callee": callee,
    }


def _fetch_method(fact: dict[str, Any], calls: list[dict[str, Any]]) -> str | None:
    file = fact.get("file")
    line = fact.get("line")
    for call in calls:
        if call.get("file") != file or call.get("line") != line:
            continue
        if not _is_fetch_callee(str(call.get("callee") or "")):
            continue
        args = call.get("arguments") or []
        if len(args) >= 2 and args[1]:
            found = _METHOD_IN_OPTIONS.search(str(args[1]))
            if found:
                return found.group(1).upper()
        if args:
            raw = args[0]
            if raw and not fact.get("url"):
                fact["url"] = raw
    return None
