from __future__ import annotations

from typing import Any

FRONTEND_ROOT_DIRS = {
    "src",
    "public",
    "client",
    "frontend",
    "web",
    "static",
    "assets",
    "components",
    "hooks",
    "styles",
    "css",
}
BACKEND_ROOT_DIRS = {"backend", "server", "api", "app", "services"}
WORKSPACE_MARKERS = {
    "pnpm-workspace.yaml",
    "lerna.json",
    "nx.json",
    "turbo.json",
}


def _norm(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


def _basename(path: str) -> str:
    return _norm(path).split("/")[-1]


def detect_structure(tree_paths: list[str]) -> dict[str, Any]:
    """High-level repo layout from paths only (reuses fullstack folder heuristics)."""
    paths = [_norm(p) for p in tree_paths if p]
    names = {_basename(p).lower() for p in paths}
    top_dirs = sorted(
        {
            p.split("/")[0]
            for p in paths
            if "/" in p and p.split("/")[0] not in {".github", ".vscode"}
        }
    )

    has_frontend_dir = any(d.lower() in FRONTEND_ROOT_DIRS for d in top_dirs)
    has_backend_dir = any(d.lower() in BACKEND_ROOT_DIRS for d in top_dirs)
    package_jsons = [p for p in paths if _basename(p).lower() == "package.json"]
    py_manifests = [
        p
        for p in paths
        if _basename(p).lower() in {"requirements.txt", "pyproject.toml", "Pipfile"}
    ]

    is_monorepo = (
        bool(names & {m.lower() for m in WORKSPACE_MARKERS})
        or len(package_jsons) > 1
        or (has_frontend_dir and has_backend_dir)
    )

    looks_frontend = has_frontend_dir or any(
        n.startswith("next.config.") or n.startswith("vite.config.") or n == "index.html"
        for n in names
    )
    looks_backend = has_backend_dir or any(
        n in {"main.py", "app.py", "manage.py", "wsgi.py", "asgi.py", "go.mod", "pom.xml"}
        for n in names
    )

    if looks_frontend and looks_backend:
        layout = "fullstack"
    elif looks_frontend:
        layout = "frontend"
    elif looks_backend:
        layout = "backend"
    elif py_manifests or package_jsons:
        layout = "library"
    else:
        layout = "unknown"

    return {
        "layout": layout,
        "is_monorepo": is_monorepo,
        "top_level_dirs": top_dirs[:40],
        "has_frontend_paths": looks_frontend,
        "has_backend_paths": looks_backend,
        "package_json_count": len(package_jsons),
        "python_manifest_count": len(py_manifests),
    }
