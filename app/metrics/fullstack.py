from __future__ import annotations

import json
import re
from typing import Any

from app.metrics.base import Metric, MetricContext, MetricResult

FRONTEND_MARKERS = (
    re.compile(r"(^|/)package\.json$"),
    re.compile(r"(^|/)src/App\.(jsx?|tsx?)$"),
    re.compile(r"(^|/)public/index\.html$"),
    re.compile(r"(^|/)index\.html$"),
    re.compile(r"(^|/)vite\.config\.(js|ts|mjs)$"),
    re.compile(r"(^|/)next\.config\.(js|mjs|ts)$"),
)

BACKEND_MARKERS = (
    re.compile(r"(^|/)requirements\.txt$"),
    re.compile(r"(^|/)pyproject\.toml$"),
    re.compile(r"(^|/)main\.py$"),
    re.compile(r"(^|/)app\.py$"),
    re.compile(r"(^|/)pom\.xml$"),
    re.compile(r"(^|/)go\.mod$"),
    re.compile(r"(^|/)Cargo\.toml$"),
    re.compile(r"(^|/)server/"),
    re.compile(r"(^|/)backend/"),
    re.compile(r"(^|/)api/"),
)


def _any_match(paths: list[str], patterns: tuple[re.Pattern[str], ...]) -> bool:
    return any(p.search(path) for path in paths for p in patterns)


def _guess_frontend(paths: list[str], manifests: dict[str, str]) -> str | None:
    blob = "\n".join(manifests.values()).lower()
    joined = "\n".join(paths).lower()
    if "next" in blob or "next.config" in joined:
        return "Next.js"
    if "react" in blob or re.search(r"src/app\.(jsx|tsx)$", joined, re.I):
        return "React"
    if "vue" in blob:
        return "Vue"
    if "svelte" in blob:
        return "Svelte"
    if "angular" in blob:
        return "Angular"
    if any(p.endswith("package.json") for p in paths):
        return "JavaScript/Node"
    return None


def _guess_backend(paths: list[str], manifests: dict[str, str]) -> str | None:
    blob = "\n".join(manifests.values()).lower()
    joined = "\n".join(paths).lower()
    if "fastapi" in blob:
        return "FastAPI"
    if "django" in blob:
        return "Django"
    if "flask" in blob:
        return "Flask"
    if "express" in blob:
        return "Express"
    if "spring" in blob or any(p.endswith("pom.xml") for p in paths):
        return "Java/Spring"
    if any(p.endswith("go.mod") for p in paths):
        return "Go"
    if any(p.endswith("Cargo.toml") for p in paths):
        return "Rust"
    if "main.py" in joined or "requirements.txt" in joined or "pyproject.toml" in joined:
        return "Python"
    if any("/server/" in p or "/backend/" in p or "/api/" in p for p in paths):
        return "Server"
    return None


class FullstackMetric(Metric):
    name = "fullstack"
    tier = "static"
    description = "Detects frontend + backend markers and guesses stacks."
    output_schema = {
        "type": "object",
        "properties": {
            "is_fullstack": {"type": "boolean"},
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
        },
    }

    async def run(self, ctx: MetricContext) -> MetricResult:
        paths = [t["path"] for t in ctx.snapshot.tree]
        fe = _any_match(paths, FRONTEND_MARKERS)
        be = _any_match(paths, BACKEND_MARKERS)

        # package.json alone isn't enough for frontend if it's a pure Node API —
        # require HTML/App marker OR a known UI framework dep.
        if fe and not any(
            re.search(r"(App\.(jsx?|tsx?)|index\.html|vite\.config|next\.config)", p) for p in paths
        ):
            ui_hint = False
            for content in ctx.snapshot.package_manifests.values():
                try:
                    data = json.loads(content)
                except json.JSONDecodeError:
                    continue
                deps = {**(data.get("dependencies") or {}), **(data.get("devDependencies") or {})}
                if any(k in deps for k in ("react", "vue", "svelte", "next", "@angular/core")):
                    ui_hint = True
                    break
            fe = ui_hint

        data: dict[str, Any] = {
            "is_fullstack": bool(fe and be),
            "frontend_detected": {
                "present": fe,
                "stack_guess": _guess_frontend(paths, ctx.snapshot.package_manifests) if fe else None,
            },
            "backend_detected": {
                "present": be,
                "stack_guess": _guess_backend(paths, ctx.snapshot.package_manifests) if be else None,
            },
        }
        return MetricResult(name=self.name, status="ok", data=data)
