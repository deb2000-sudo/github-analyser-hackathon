"""GitHub URL → repository → filtered files → languages → manifests → inventory → source facts → call graph.

Phase 7 adds simple value flow. AI-specific classification is not built here.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

from app.analysis.call_graph import CallGraph, build_call_graph
from app.analysis.data_flow import DataFlowGraph, build_data_flow
from app.analysis.extract import extract_source_facts
from app.analysis.inventory import FileInventory, build_inventory
from app.analysis.manifests import parse_dependencies
from app.analysis.structure import detect_structure

if TYPE_CHECKING:
    from app.github.client import RepoSnapshot

_MAX_PUBLIC_FACTS = 400


@dataclass
class CodeFacts:
    """Pipeline snapshot plus language-independent source facts."""

    repository: dict[str, Any]
    filtered_files: list[dict[str, Any]]
    languages: dict[str, int]
    manifests: dict[str, Any]
    inventory: dict[str, Any]
    structure: dict[str, Any]
    file_inventory: FileInventory
    source_facts: list[dict[str, Any]]
    parse_warnings: list[dict[str, Any]]
    call_graph: CallGraph
    data_flow: DataFlowGraph

    def to_public_dict(self) -> dict[str, Any]:
        """Job result.analysis — normalized facts only, no AST / Tree-sitter nodes."""
        counts = dict(Counter(str(f.get("fact_type") or "unknown") for f in self.source_facts))
        truncated = len(self.source_facts) > _MAX_PUBLIC_FACTS
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
            "source_facts": {
                "counts": counts,
                "fact_count": len(self.source_facts),
                "warnings": self.parse_warnings,
                "facts": self.source_facts[:_MAX_PUBLIC_FACTS],
                "truncated": truncated,
            },
            "call_graph": self.call_graph.to_public_dict(),
            "data_flow": self.data_flow.to_public_dict(),
        }

    def summary(self) -> dict[str, Any]:
        return {
            "repo": self.repository.get("full_name"),
            "file_count": self.inventory.get("file_count", 0),
            "languages": self.languages,
            "layout": (self.structure or {}).get("layout"),
            "npm_top": (self.manifests.get("npm") or [])[:20],
            "python_top": (self.manifests.get("python") or [])[:20],
            "source_fact_count": len(self.source_facts),
            "parse_warning_count": len(self.parse_warnings),
            "call_graph_nodes": self.call_graph.graph.number_of_nodes(),
            "call_graph_edges": self.call_graph.graph.number_of_edges(),
            "data_flow_nodes": self.data_flow.graph.number_of_nodes(),
            "data_flow_edges": self.data_flow.graph.number_of_edges(),
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

    file_inventory = FileInventory.from_raw(raw_inventory)
    contents = dict(snapshot.file_contents or {})
    for path, content in (snapshot.package_manifests or {}).items():
        contents.setdefault(path, content)
    source_facts, parse_warnings = extract_source_facts(
        contents,
        file_inventory=file_inventory,
    )
    call_graph = build_call_graph(source_facts)
    data_flow = build_data_flow(source_facts)
    return CodeFacts(
        repository=repository,
        filtered_files=files,
        languages=languages,
        manifests=parsed_manifests,
        inventory=inventory,
        structure=structure,
        file_inventory=file_inventory,
        source_facts=source_facts,
        parse_warnings=parse_warnings,
        call_graph=call_graph,
        data_flow=data_flow,
    )
