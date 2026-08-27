from __future__ import annotations

import json
import re
from typing import Any

from app.metrics.base import Metric, MetricContext, MetricResult

UI_PACKAGES = (
    "react",
    "react-dom",
    "vue",
    "svelte",
    "next",
    "@angular/core",
    "nuxt",
    "gatsby",
    "preact",
    "solid-js",
    "@remix-run/react",
)
FRONTEND_BUILD_FILES = (
    "vite.config.js",
    "vite.config.ts",
    "vite.config.mjs",
    "next.config.js",
    "next.config.mjs",
    "next.config.ts",
    "angular.json",
    "svelte.config.js",
    "astro.config.mjs",
)
NODE_SERVER_PACKAGES = (
    "express",
    "fastify",
    "koa",
    "@hapi/hapi",
    "hapi",
    "@nestjs/core",
    "hono",
    "restify",
)
PYTHON_SERVER_PACKAGES = (
    "fastapi",
    "flask",
    "django",
    "starlette",
    "uvicorn",
    "gunicorn",
    "tornado",
    "sanic",
)
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

CORE_CONFIG_NAMES = {
    "package.json",
    "requirements.txt",
    "pyproject.toml",
    "Pipfile",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "Cargo.toml",
    "composer.json",
    "Gemfile",
    "vite.config.js",
    "vite.config.ts",
    "vite.config.mjs",
    "next.config.js",
    "next.config.mjs",
    "next.config.ts",
    "vercel.json",
    "netlify.toml",
    "angular.json",
    "svelte.config.js",
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    "manage.py",
    "server.js",
    "server.ts",
    "server.py",
    "main.py",
    "app.py",
    "wsgi.py",
    "asgi.py",
}

NODE_SERVER_CONTENT = re.compile(
    r"""(?x)
    (?:from\s+['"]express['"]|require\s*\(\s*['"]express['"])
    |(?:from\s+['"]fastify['"]|require\s*\(\s*['"]fastify['"])
    |(?:from\s+['"]koa['"]|require\s*\(\s*['"]koa['"])
    |\.listen\s*\(
    |createServer\s*\(
    """,
    re.I,
)
PYTHON_SERVER_CONTENT = re.compile(
    r"""(?x)
    FastAPI\s*\(|Flask\s*\(
    |\bfrom\s+fastapi\b|\bimport\s+fastapi\b
    |\bfrom\s+flask\b|\bimport\s+flask\b
    |\bfrom\s+django\b
    |@app\.(?:get|post|put|patch|delete|route)\s*\(
    |urlpatterns\s*=
    """,
    re.I,
)
NEXT_ROUTE_HANDLER = re.compile(
    r"export\s+(async\s+)?function\s+(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\b"
)
VERCEL_SERVERLESS = re.compile(
    r"""(?x)
    export\s+default\s+(async\s+)?function\s*\(\s*(req|request)
    |module\.exports\s*=\s*(async\s+)?function\s*\(\s*(req|request)
    """,
    re.I,
)
NEXT_API_PATH = re.compile(
    r"(^|/)(src/)?(app|pages)/api/(.+/)?(route|index|\w+)\.(t|j)sx?$"
)


def _norm(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


def _basename(path: str) -> str:
    return _norm(path).split("/")[-1]


def _is_frontend_scoped(path: str) -> bool:
    """True for UI trees (src/, public/, …) — not a standalone server."""
    parts = _norm(path).lower().split("/")
    if not parts:
        return False
    if NEXT_API_PATH.search(_norm(path).lower()):
        return False
    return parts[0] in FRONTEND_ROOT_DIRS or "src" in parts[:-1]


def _is_next_api_route(path: str) -> bool:
    return bool(NEXT_API_PATH.search(_norm(path).lower()))


def _is_root_vercel_fn(path: str) -> bool:
    """Vercel serverless: api/hello.js at repo root, not src/api."""
    return bool(re.match(r"^api/[^/]+\.(js|ts|mjs|cjs)$", _norm(path).lower()))


def _parse_npm_deps(manifests: dict[str, str]) -> dict[str, str]:
    deps: dict[str, str] = {}
    for path, content in manifests.items():
        if not _basename(path).lower() == "package.json":
            continue
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        for key in ("dependencies", "devDependencies", "peerDependencies"):
            block = data.get(key) or {}
            if isinstance(block, dict):
                for name, ver in block.items():
                    deps[str(name).lower()] = str(ver)
    return deps


def _python_packages(manifests: dict[str, str]) -> set[str]:
    found: set[str] = set()
    for path, content in manifests.items():
        lower = _norm(path).lower()
        if lower.endswith("requirements.txt") or lower.endswith("requirements-dev.txt"):
            for line in content.splitlines():
                line = line.strip()
                if not line or line.startswith("#") or line.startswith("-"):
                    continue
                pkg = re.split(r"[<=>!~\s\[]", line, maxsplit=1)[0].strip().lower()
                if pkg:
                    found.add(pkg)
        elif lower.endswith("pyproject.toml"):
            blob = content.lower()
            for pkg in PYTHON_SERVER_PACKAGES:
                if re.search(rf'["\']{re.escape(pkg)}["\']|{pkg}\s*=', blob):
                    found.add(pkg)
    return found


def _guess_frontend_stack(paths: list[str], deps: dict[str, str]) -> str | None:
    names = {_basename(p).lower() for p in paths}
    if "next" in deps or any(n.startswith("next.config.") for n in names):
        return "Next.js"
    if "react" in deps or "react-dom" in deps:
        return "React"
    if "vue" in deps:
        return "Vue"
    if "svelte" in deps:
        return "Svelte"
    if "@angular/core" in deps:
        return "Angular"
    if "nuxt" in deps:
        return "Nuxt"
    if any(n in names for n in FRONTEND_BUILD_FILES) or any(
        p.lower().endswith("index.html") for p in paths
    ):
        return "JavaScript"
    if any(_basename(p) == "package.json" for p in paths) and any(
        k in deps for k in UI_PACKAGES
    ):
        return "JavaScript"
    return None


def _guess_backend_stack(
    deps: dict[str, str],
    py_pkgs: set[str],
    paths: list[str],
    contents: dict[str, str],
) -> str | None:
    blob = "\n".join(contents.values())
    if "fastapi" in py_pkgs or re.search(r"FastAPI\s*\(", blob):
        return "FastAPI"
    if "django" in py_pkgs or "from django" in blob.lower():
        return "Django"
    if "flask" in py_pkgs or re.search(r"Flask\s*\(", blob):
        return "Flask"
    if "@nestjs/core" in deps:
        return "NestJS"
    if "express" in deps or re.search(r"['\"]express['\"]", blob):
        return "Express"
    if "fastify" in deps:
        return "Fastify"
    if any(p.endswith("pom.xml") for p in paths) or "spring" in blob.lower():
        return "Java/Spring"
    if any(p.endswith("go.mod") for p in paths):
        return "Go"
    if any(p.endswith("Cargo.toml") for p in paths):
        return "Rust"
    if py_pkgs & set(PYTHON_SERVER_PACKAGES):
        return "Python"
    if any(_is_next_api_route(p) for p in paths) or any(
        NEXT_ROUTE_HANDLER.search(c) for c in contents.values()
    ):
        return "Next.js API"
    if any(_is_root_vercel_fn(p) for p in paths):
        return "Serverless"
    return "Server"


def _has_frontend(paths: list[str], deps: dict[str, str], contents: dict[str, str]) -> bool:
    names = {_basename(p).lower() for p in paths}
    if any(pkg in deps for pkg in UI_PACKAGES):
        return True
    if any(n in names for n in FRONTEND_BUILD_FILES):
        return True
    if any(re.search(r"(^|/)index\.html$", p, re.I) for p in paths) and any(
        "/src/" in f"/{_norm(p).lower()}/" or _norm(p).lower().startswith("src/")
        for p in paths
    ):
        return True
    if any(re.search(r"(^|/)src/App\.(jsx?|tsx?)$", p) for p in paths):
        return True
    joined = "\n".join(contents.get(p, "") for p in contents if re.search(r"App\.(jsx?|tsx?)$", p))
    if re.search(r"from\s+['\"]react['\"]|react-dom", joined):
        return True
    return False


def _content_looks_like_server(path: str, content: str) -> bool:
    if not content or not content.strip():
        return False
    if _is_frontend_scoped(path) and not _is_next_api_route(path):
        return False
    if PYTHON_SERVER_CONTENT.search(content):
        return True
    if NODE_SERVER_CONTENT.search(content):
        return True
    if _is_next_api_route(path) and (
        NEXT_ROUTE_HANDLER.search(content) or "NextRequest" in content or "NextResponse" in content
    ):
        return True
    if _is_root_vercel_fn(path) and VERCEL_SERVERLESS.search(content):
        return True
    return False


def _has_backend(
    paths: list[str],
    deps: dict[str, str],
    py_pkgs: set[str],
    contents: dict[str, str],
) -> tuple[bool, list[str]]:
    evidence: list[str] = []

    if py_pkgs & set(PYTHON_SERVER_PACKAGES):
        evidence.append("python-web-framework")
    if any(pkg in deps for pkg in NODE_SERVER_PACKAGES):
        evidence.append("node-web-framework")
    if any(p.endswith(("go.mod", "pom.xml")) for p in paths) and not _has_only_frontend_java_go(
        paths
    ):
        # go.mod / pom.xml at repo root usually means a backend (or a library).
        if any(not _is_frontend_scoped(p) and p.endswith(("go.mod", "pom.xml")) for p in paths):
            evidence.append("compiled-service-manifest")

    for path, content in contents.items():
        if _content_looks_like_server(path, content):
            evidence.append(path)

    for path in paths:
        if _is_next_api_route(path):
            evidence.append(path)
            continue
        if _is_root_vercel_fn(path):
            # Need content to confirm; path alone is a hint if we have no content.
            if path not in contents:
                evidence.append(path)
            elif _content_looks_like_server(path, contents[path]):
                evidence.append(path)
            continue
        n = _norm(path).lower()
        if re.match(r"^(backend|server)/.+\.(py|js|ts|go)$", n):
            content = contents.get(path)
            if content and _content_looks_like_server(path, content):
                evidence.append(path)

    # Deduplicate while preserving order
    seen: set[str] = set()
    uniq: list[str] = []
    for item in evidence:
        if item not in seen:
            seen.add(item)
            uniq.append(item)
    return bool(uniq), uniq


def _has_only_frontend_java_go(paths: list[str]) -> bool:
    return all(_is_frontend_scoped(p) for p in paths if p.endswith(("go.mod", "pom.xml")))


def select_core_paths(paths: list[str], *, limit: int = 50) -> list[str]:
    """Pick config + entry files whose contents decide frontend vs server."""
    selected: list[str] = []
    for path in paths:
        name = _basename(path)
        lower = _norm(path).lower()
        if name in CORE_CONFIG_NAMES:
            selected.append(path)
            continue
        if name.lower() == "index.html":
            selected.append(path)
            continue
        if re.search(r"(^|/)src/App\.(jsx?|tsx?)$", path):
            selected.append(path)
            continue
        if re.search(r"(^|/)src/main\.(jsx?|tsx?)$", path):
            selected.append(path)
            continue
        if _is_next_api_route(lower) or _is_root_vercel_fn(lower):
            selected.append(path)
            continue
        if re.match(r"^(backend|server)/.+\.(py|js|ts|go)$", lower):
            selected.append(path)
            continue
        if name in {"main.py", "app.py"} and not _is_frontend_scoped(path):
            selected.append(path)
    out: list[str] = []
    seen: set[str] = set()
    for p in selected:
        if p not in seen:
            seen.add(p)
            out.append(p)
        if len(out) >= limit:
            break
    return out


class FullstackMetric(Metric):
    name = "fullstack"
    tier = "static"
    description = (
        "Classifies the repo as frontend, backend, or fullstack by reading core "
        "manifests and server entry files — not folder names like src/api."
    )
    output_schema = {
        "type": "object",
        "properties": {
            "is_fullstack": {"type": "boolean"},
            "repo_type": {
                "type": "string",
                "enum": ["frontend", "backend", "fullstack", "unknown"],
            },
            "frontend_detected": {
                "type": "object",
                "properties": {
                    "present": {"type": "boolean"},
                    "stack_guess": {"type": ["string", "null"]},
                },
            },
            "backend_detected": {
                "type": "object",
                "properties": {
                    "present": {"type": "boolean"},
                    "stack_guess": {"type": ["string", "null"]},
                },
            },
            "evidence": {
                "type": "object",
                "properties": {
                    "backend_signals": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
    }

    async def run(self, ctx: MetricContext) -> MetricResult:
        paths = [t["path"] for t in ctx.snapshot.tree]
        gh = ctx.extras.get("github_client")
        core = select_core_paths(paths)
        if gh is not None and core and not ctx.extras.get("skip_file_fetch"):
            try:
                await gh.fetch_files(ctx.snapshot, core)
            except Exception:
                pass

        manifests = dict(ctx.snapshot.package_manifests)
        for path, content in ctx.snapshot.file_contents.items():
            if _basename(path) in CORE_CONFIG_NAMES and path not in manifests:
                if _basename(path) in {
                    "package.json",
                    "requirements.txt",
                    "pyproject.toml",
                    "go.mod",
                    "pom.xml",
                    "Cargo.toml",
                }:
                    manifests[path] = content

        deps = _parse_npm_deps(manifests)
        py_pkgs = _python_packages(manifests)
        contents = dict(ctx.snapshot.file_contents)

        fe = _has_frontend(paths, deps, contents)
        be, be_evidence = _has_backend(paths, deps, py_pkgs, contents)

        if fe:
            fe_stack: str | None = _guess_frontend_stack(paths, deps)
        else:
            fe_stack = None
        be_stack = _guess_backend_stack(deps, py_pkgs, paths, contents) if be else None

        if fe and be:
            repo_type = "fullstack"
        elif fe:
            repo_type = "frontend"
        elif be:
            repo_type = "backend"
        else:
            repo_type = "unknown"

        data: dict[str, Any] = {
            "is_fullstack": bool(fe and be),
            "repo_type": repo_type,
            "frontend_detected": {
                "present": fe,
                "stack_guess": fe_stack,
            },
            "backend_detected": {
                "present": be,
                "stack_guess": be_stack,
            },
            "evidence": {"backend_signals": be_evidence},
        }
        return MetricResult(name=self.name, status="ok", data=data)
