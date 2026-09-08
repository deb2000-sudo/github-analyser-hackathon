from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any

from app.analysis.manifests import DEPENDENCY_MANIFESTS

_LANG_BY_EXT: dict[str, str] = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".rb": "ruby",
    ".php": "php",
    ".cs": "csharp",
    ".html": "html",
    ".css": "css",
    ".scss": "css",
    ".json": "json",
    ".toml": "toml",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".md": "markdown",
}

_SOURCE_EXTS = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".go",
    ".rs",
    ".java",
    ".kt",
}


@dataclass(frozen=True)
class FileEntry:
    path: str
    name: str
    language: str | None
    role: str
    size_bytes: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _norm(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


def _is_skipped(path: str) -> bool:
    parts = _norm(path).lower().split("/")
    skip_dirs = {"node_modules", ".venv", "venv", ".git", "dist", "build"}
    return any(p in skip_dirs for p in parts[:-1])


def _basename(path: str) -> str:
    return _norm(path).split("/")[-1]


def _ext(name: str) -> str:
    if name.startswith(".") and name.count(".") == 1:
        return name.lower()
    idx = name.rfind(".")
    return name[idx:].lower() if idx >= 0 else ""


def _role(name: str, ext: str) -> str:
    lower = name.lower()
    if lower in DEPENDENCY_MANIFESTS or lower in {
        "package-lock.json",
        "poetry.lock",
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
        "dockerfile",
        "docker-compose.yml",
        "docker-compose.yaml",
    }:
        return "manifest"
    if name.lower().startswith("readme"):
        return "docs"
    if ext in _SOURCE_EXTS:
        return "source"
    if ext in {".json", ".toml", ".yml", ".yaml", ".lock"} or name.lower() in {
        "dockerfile",
        ".gitignore",
        ".env.example",
    }:
        return "config"
    if ext in {".md", ".rst", ".txt"}:
        return "docs"
    return "other"


def build_inventory(tree: list[dict[str, Any]], *, max_paths: int = 2000) -> dict[str, Any]:
    """Typed inventory from GitHub recursive tree blobs (no clone)."""
    entries: list[FileEntry] = []
    for node in tree:
        if node.get("type") and node.get("type") != "blob":
            continue
        path = node.get("path") or ""
        if not path or _is_skipped(path):
            continue
        name = _basename(path)
        ext = _ext(name)
        size = node.get("size")
        try:
            size_i = int(size) if size is not None else None
        except (TypeError, ValueError):
            size_i = None
        entries.append(
            FileEntry(
                path=_norm(path),
                name=name,
                language=_LANG_BY_EXT.get(ext),
                role=_role(name, ext),
                size_bytes=size_i,
            )
        )

    by_language = Counter(e.language or "unknown" for e in entries)
    by_role = Counter(e.role for e in entries)
    return {
        "file_count": len(entries),
        "by_language": dict(by_language),
        "by_role": dict(by_role),
        "files": [e.to_dict() for e in entries[:max_paths]],
        "truncated": len(entries) > max_paths,
    }
