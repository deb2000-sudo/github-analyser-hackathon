"""Phase 5: whether frontend HTTP clients talk to backend routes. Uses Phase 4 output."""

from __future__ import annotations

from typing import Any


def match_frontend_backend(
    backend_routes: list[dict[str, Any]],
    frontend_requests: list[dict[str, Any]],
) -> dict[str, Any]:
    """Match HTTP method + normalized path. Unresolved dynamic URLs stay unknown."""
    connections: list[dict[str, Any]] = []
    method_mismatches: list[dict[str, Any]] = []
    matched_fe: set[int] = set()
    matched_be: set[int] = set()

    for i, client in enumerate(frontend_requests):
        if not client.get("resolved") or not client.get("path"):
            continue
        for j, route in enumerate(backend_routes):
            if not route.get("path"):
                continue
            if client["path"] != route["path"]:
                continue
            if client.get("method") == route.get("method"):
                confidence = 0.97
                if client.get("dynamic_base") or route.get("dynamic_base"):
                    confidence = 0.90
                connections.append(
                    {
                        "method": client["method"],
                        "path": client["path"],
                        "frontend_file": client.get("file"),
                        "frontend_line": client.get("line"),
                        "backend_file": route.get("file"),
                        "backend_line": route.get("line"),
                        "confidence": confidence,
                    }
                )
                matched_fe.add(i)
                matched_be.add(j)
            else:
                method_mismatches.append(
                    {
                        "path": client["path"],
                        "frontend_method": client.get("method"),
                        "backend_method": route.get("method"),
                        "frontend_file": client.get("file"),
                        "frontend_line": client.get("line"),
                        "backend_file": route.get("file"),
                        "backend_line": route.get("line"),
                    }
                )

    unmatched_frontend: list[dict[str, Any]] = []
    for i, client in enumerate(frontend_requests):
        if i in matched_fe:
            continue
        if not client.get("resolved") or not client.get("path"):
            unmatched_frontend.append(
                {
                    "method": client.get("method"),
                    "path": client.get("path"),
                    "file": client.get("file"),
                    "line": client.get("line"),
                    "raw": client.get("raw"),
                    "reason": "unresolved_url",
                }
            )
            continue
        if any(
            mm.get("frontend_file") == client.get("file")
            and mm.get("frontend_line") == client.get("line")
            for mm in method_mismatches
        ):
            unmatched_frontend.append(
                {
                    "method": client.get("method"),
                    "path": client.get("path"),
                    "file": client.get("file"),
                    "line": client.get("line"),
                    "raw": client.get("raw"),
                    "reason": "method_mismatch",
                }
            )
            continue
        unmatched_frontend.append(
            {
                "method": client.get("method"),
                "path": client.get("path"),
                "file": client.get("file"),
                "line": client.get("line"),
                "raw": client.get("raw"),
                "reason": "no_backend_match",
            }
        )

    unmatched_backend: list[dict[str, Any]] = []
    for j, route in enumerate(backend_routes):
        if j in matched_be:
            continue
        unmatched_backend.append(
            {
                "method": route.get("method"),
                "path": route.get("path"),
                "file": route.get("file"),
                "line": route.get("line"),
                "reason": "never_called",
            }
        )

    connected: bool | str
    if connections:
        connected = True
    elif frontend_requests and all(not c.get("resolved") for c in frontend_requests):
        connected = "unknown"
    elif not frontend_requests and not backend_routes:
        connected = "unknown"
    else:
        connected = False

    return {
        "connected": connected,
        "connections": connections,
        "unmatched_frontend": unmatched_frontend,
        "unmatched_backend": unmatched_backend,
        "method_mismatches": method_mismatches,
    }
