"""Parse repository source files into language-independent Code Facts.

Later analyzers must consume these facts, not Python AST or Tree-sitter nodes.
"""

from __future__ import annotations

from typing import Any

from app.analysis.extract_js import extract_js_ts
from app.analysis.extract_python import extract_python
from app.analysis.inventory import FileInventory
from app.analysis.source_models import ParseWarning

PARSEABLE_EXTS = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
}
_SKIP_DIRS = {"node_modules", ".venv", "venv", ".git", "dist", "build"}
MAX_SOURCE_BYTES = 200_000
MAX_PARSEABLE_PATHS = 80


def _norm(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


def language_for(path: str) -> str | None:
    name = _norm(path).rsplit("/", 1)[-1].lower()
    if name.endswith(".min.js") or name.endswith(".min.mjs"):
        return None
    idx = name.rfind(".")
    ext = name[idx:] if idx >= 0 else ""
    return PARSEABLE_EXTS.get(ext)


def _is_skipped_path(path: str) -> bool:
    parts = _norm(path).lower().split("/")
    return any(part in _SKIP_DIRS for part in parts[:-1])


def select_parseable_paths(paths: list[str], *, limit: int = MAX_PARSEABLE_PATHS) -> list[str]:
    """Source files Phase 3 can parse (Python / JS / TS)."""
    selected: list[str] = []
    seen: set[str] = set()
    for path in paths:
        if path in seen or _is_skipped_path(path) or language_for(path) is None:
            continue
        seen.add(path)
        selected.append(path)
        if len(selected) >= limit:
            break
    return selected


def extract_source_facts(
    contents: dict[str, str],
    *,
    file_inventory: FileInventory | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    allowed: set[str] | None = None
    if file_inventory is not None:
        allowed = {entry.path for entry in file_inventory.files if language_for(entry.path)}

    facts: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    for path, source in contents.items():
        if not source or language_for(path) is None:
            continue
        if allowed is not None and path not in allowed:
            continue
        if _is_skipped_path(path):
            continue
        file_facts, file_warnings = extract_file_facts(path, source)
        facts.extend(file_facts)
        warnings.extend(file_warnings)
    return facts, warnings


def extract_file_facts(path: str, source: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    language = language_for(path)
    if language is None:
        return [], []
    if len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
        warning = ParseWarning(
            file=path,
            language=language,
            kind="skipped_too_large",
            message=f"source exceeds {MAX_SOURCE_BYTES} bytes",
        )
        return [], [warning.to_dict()]
    try:
        if language == "python":
            return extract_python(path, source)
        return extract_js_ts(path, source, language=language)
    except Exception as exc:  # noqa: BLE001
        warning = ParseWarning(
            file=path,
            language=language,
            kind="parse_error",
            message=str(exc),
        )
        return [], [warning.to_dict()]
