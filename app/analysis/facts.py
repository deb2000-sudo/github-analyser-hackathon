from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.analysis.inventory import build_inventory
from app.analysis.manifests import parse_dependencies
from app.analysis.structure import detect_structure
from app.github.client import RepoSnapshot


@dataclass
class CodeFacts:
    """GitHub URL → repository → filtered files → languages → manifests → inventory.

    AST is intentionally omitted in this phase.
    """

    repository: dict[str, Any]
    filtered_files: list[dict[str, Any]]
    languages: dict[str, int]
    manifests: dict[str, Any]
    inventory: dict[str, Any]
    structure: dict[str, Any]

    def to_public_dict(self) -> dict[str, Any]:
        """Job result.analysis — pipeline stages only, no AST."""
        return {
            "repository": self.repository,
            "filtered_files": {
                "count": len(self.filtered_files),
                "paths": [f.get("path") for f in self.filtered_files[:200] if f.get("path")],
                "truncated": len(self.filtered_files) > 200,
            },
            "languages": self.languages,
            "manifests": {
                "npm": (self.manifests.get("npm") or [])[:80],
                "python": (self.manifests.get("python") or [])[:80],
                "go": (self.manifests.get("go") or [])[:40],
                "manifest_paths": self.manifests.get("manifest_paths") or [],
            },
            "inventory": self.inventory,
        }

    def summary(self) -> dict[str, Any]:
        return {
            "repo": self.repository.get("full_name"),
            "file_count": self.inventory.get("file_count", 0),
            "languages": self.languages,
            "layout": (self.structure or {}).get("layout"),
            "npm_top": (self.manifests.get("npm") or [])[:20],
            "python_top": (self.manifests.get("python") or [])[:20],
        }


def build_code_facts(snapshot: RepoSnapshot) -> CodeFacts:
    tree = list(snapshot.tree or [])
    raw_inventory = build_inventory(tree)
    files = list(raw_inventory.get("files") or [])
    languages = dict(raw_inventory.get("by_language") or {})

    manifests = dict(snapshot.package_manifests or {})
    for path, content in (snapshot.file_contents or {}).items():
        name = path.replace("\\", "/").split("/")[-1]
        if name in {
            "package.json",
            "requirements.txt",
            "pyproject.toml",
            "go.mod",
            "Pipfile",
        } and path not in manifests:
            manifests[path] = content
    parsed_manifests = parse_dependencies(manifests)

    paths = [t.get("path") or "" for t in tree if t.get("path")]
    structure = detect_structure(paths)

    inventory = {
        "file_count": raw_inventory.get("file_count", 0),
        "by_language": languages,
        "by_role": raw_inventory.get("by_role") or {},
        "truncated": bool(raw_inventory.get("truncated")),
        "sample_paths": [f.get("path") for f in files[:80] if f.get("path")],
        "layout": structure.get("layout"),
        "is_monorepo": structure.get("is_monorepo"),
    }

    ref = snapshot.ref
    repository = {
        "owner": ref.owner,
        "name": ref.name,
        "full_name": ref.full_name,
        "default_branch": ref.default_branch,
        "commit_sha": ref.commit_sha,
    }

    return CodeFacts(
        repository=repository,
        filtered_files=files,
        languages=languages,
        manifests=parsed_manifests,
        inventory=inventory,
        structure=structure,
    )
