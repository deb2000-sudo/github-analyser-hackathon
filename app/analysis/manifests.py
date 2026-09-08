from __future__ import annotations

import json
import re
from typing import Any

# Shared with GithubClient snapshot fetch and fullstack core-config detection.
MANIFEST_NAMES = frozenset(
    {
        "package.json",
        "package-lock.json",
        "requirements.txt",
        "pyproject.toml",
        "Pipfile",
        "poetry.lock",
        "go.mod",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "Cargo.toml",
        "composer.json",
        "Gemfile",
        "environment.yml",
        "conda.yml",
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
)

DEPENDENCY_MANIFESTS = frozenset(
    {
        "package.json",
        "requirements.txt",
        "requirements-dev.txt",
        "pyproject.toml",
        "Pipfile",
        "go.mod",
        "pom.xml",
        "Cargo.toml",
        "composer.json",
        "Gemfile",
    }
)


def _basename(path: str) -> str:
    return path.replace("\\", "/").rstrip("/").split("/")[-1]


def _normalize_pkg(name: str) -> str:
    name = name.strip().strip("\"'").lower()
    return re.split(r"[<=>!~\s\[]", name, maxsplit=1)[0]


def parse_npm_deps(manifests: dict[str, str]) -> dict[str, str]:
    deps: dict[str, str] = {}
    for path, content in manifests.items():
        if _basename(path).lower() != "package.json":
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


def parse_python_packages(manifests: dict[str, str]) -> list[str]:
    found: set[str] = set()
    for path, content in manifests.items():
        lower = path.replace("\\", "/").lower()
        name = _basename(path).lower()
        if name in {"requirements.txt", "requirements-dev.txt"} or lower.endswith(
            "/requirements.txt"
        ):
            for line in content.splitlines():
                line = line.strip()
                if not line or line.startswith("#") or line.startswith("-"):
                    continue
                pkg = _normalize_pkg(line)
                if pkg:
                    found.add(pkg)
        elif name == "pyproject.toml":
            for m in re.finditer(
                r'["\']([a-zA-Z0-9_.\-@/]+)["\']\s*[=>~<]|^\s*([a-zA-Z0-9_.\-]+)\s*[=>~<]',
                content,
                re.M,
            ):
                pkg = _normalize_pkg(m.group(1) or m.group(2) or "")
                if pkg:
                    found.add(pkg)
            for line in content.splitlines():
                m = re.match(r"^\s*([a-zA-Z0-9_.\-]+)\s*=", line)
                if m:
                    pkg = _normalize_pkg(m.group(1))
                    if pkg and pkg not in {"name", "version", "description", "readme"}:
                        found.add(pkg)
    return sorted(found)


def parse_go_modules(manifests: dict[str, str]) -> list[str]:
    mods: set[str] = set()
    for path, content in manifests.items():
        if _basename(path).lower() != "go.mod":
            continue
        for line in content.splitlines():
            parts = line.strip().split()
            if len(parts) >= 2 and parts[0] in {"require", "module"}:
                mods.add(parts[1])
            elif len(parts) >= 1 and "/" in parts[0] and not parts[0].startswith("//"):
                mods.add(parts[0])
    return sorted(mods)


def parse_dependencies(manifests: dict[str, str]) -> dict[str, Any]:
    """Normalized dependency lists from already-fetched manifest contents."""
    npm = parse_npm_deps(manifests)
    return {
        "npm": sorted(npm.keys()),
        "npm_versions": npm,
        "python": parse_python_packages(manifests),
        "go": parse_go_modules(manifests),
        "manifest_paths": sorted(
            p for p in manifests if _basename(p) in DEPENDENCY_MANIFESTS
        ),
    }
