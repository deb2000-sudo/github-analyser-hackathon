from __future__ import annotations

import re
from typing import Any

README_RE = re.compile(r"(^|/)readme(\.[a-z0-9]+)?$", re.I)

SETUP_KEYWORDS = (
    "install",
    "setup",
    "getting started",
    "quick start",
    "run locally",
    "local development",
    "environment variable",
    ".env",
    "docker",
    "docker-compose",
    "npm install",
    "pnpm install",
    "yarn install",
    "pip install",
    "uv sync",
    "poetry install",
    "python -m",
    "npm run dev",
    "npm start",
    "uvicorn",
    "make dev",
    "cp .env",
)


def find_readme_content(tree: list[dict[str, Any]], file_contents: dict[str, str]) -> tuple[str | None, str | None]:
    paths = [t["path"] for t in tree if t.get("type") == "blob" and README_RE.search(t["path"].split("/")[-1])]
    paths.sort(key=lambda p: (p.count("/"), len(p)))
    for path in paths:
        content = file_contents.get(path)
        if content and content.strip():
            return path, content
    return None, None


def analyze_readme(content: str | None) -> dict[str, Any]:
    if not content or not content.strip():
        return {
            "readme_found": False,
            "readme_quality_score": 0.0,
            "readme_has_local_setup": False,
            "setup_signals": [],
            "reasoning": "No README found or README is empty.",
        }

    lower = content.lower()
    hits = [kw for kw in SETUP_KEYWORDS if kw in lower]
    has_setup = len(hits) >= 2
    has_env = ".env" in lower or "environment variable" in lower
    has_install = any(k in lower for k in ("install", "pip install", "npm install", "uv sync", "poetry install"))
    has_run = any(k in lower for k in ("npm run", "uvicorn", "python -m", "docker-compose up", "make dev"))

    score = 0.0
    notes: list[str] = []
    if has_install:
        score += 3.0
        notes.append("install steps mentioned")
    if has_run:
        score += 3.0
        notes.append("run/dev command documented")
    if has_env:
        score += 2.0
        notes.append("environment configuration documented")
    if has_setup:
        score += 2.0
        notes.append("local setup section detected")

    score = min(10.0, score)
    if score >= 8.0:
        reasoning = f"README documents local setup well ({', '.join(notes) or 'clear structure'})."
    elif score >= 4.0:
        reasoning = f"README partially covers setup — missing: { _missing_setup_notes(has_install, has_run, has_env) }."
    else:
        reasoning = "README lacks clear install/run/env instructions for local setup."

    return {
        "readme_found": True,
        "readme_quality_score": round(score, 1),
        "readme_has_local_setup": has_setup and has_install and has_run,
        "setup_signals": hits[:8],
        "reasoning": reasoning,
    }


def _missing_setup_notes(has_install: bool, has_run: bool, has_env: bool) -> str:
    missing = []
    if not has_install:
        missing.append("install steps")
    if not has_run:
        missing.append("how to run locally")
    if not has_env:
        missing.append("env/config notes")
    return ", ".join(missing) or "some setup details"
