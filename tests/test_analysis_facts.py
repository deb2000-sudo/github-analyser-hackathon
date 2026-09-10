"""Phase 1 + 3: inventory through normalized source facts. No call graph."""

from __future__ import annotations

from app.analysis.facts import build_code_facts
from app.analysis.inventory import build_inventory
from app.analysis.manifests import MANIFEST_NAMES, parse_dependencies
from app.analysis.structure import detect_structure
from app.github.client import RepoRef, RepoSnapshot


def test_inventory_skips_vendor_and_classifies_roles():
    tree = [
        {"path": "frontend/src/App.tsx", "type": "blob", "size": 120},
        {"path": "frontend/package.json", "type": "blob"},
        {"path": "backend/main.py", "type": "blob"},
        {"path": "README.md", "type": "blob"},
        {"path": "node_modules/react/index.js", "type": "blob"},
        {"path": "frontend/dist/bundle.js", "type": "blob"},
    ]
    inv = build_inventory(tree)
    paths = {f["path"] for f in inv["files"]}
    assert "frontend/src/App.tsx" in paths
    assert "node_modules/react/index.js" not in paths
    assert "frontend/dist/bundle.js" not in paths
    assert inv["by_language"].get("typescript") == 1
    assert inv["by_language"].get("python") == 1
    roles = {f["path"]: f["role"] for f in inv["files"]}
    assert roles["frontend/package.json"] == "manifest"
    assert roles["backend/main.py"] == "source"
    assert roles["README.md"] == "docs"


def test_structure_detects_fullstack_monorepo():
    paths = [
        "frontend/package.json",
        "frontend/src/App.tsx",
        "backend/requirements.txt",
        "backend/main.py",
    ]
    info = detect_structure(paths)
    assert info["layout"] == "fullstack"
    assert info["is_monorepo"] is True


def test_parse_dependencies_npm_and_python():
    deps = parse_dependencies(
        {
            "frontend/package.json": '{"dependencies":{"react":"18.0.0","express":"4.18.0"}}',
            "backend/requirements.txt": "fastapi==0.115.0\nopenai==1.0.0\n",
        }
    )
    assert "react" in deps["npm"]
    assert "express" in deps["npm"]
    assert "fastapi" in deps["python"]
    assert "openai" in deps["python"]


def test_build_code_facts_pipeline_with_source_facts():
    snapshot = RepoSnapshot(
        ref=RepoRef("o", "r", commit_sha="abc123"),
        tree=[
            {"path": "frontend/package.json", "type": "blob"},
            {"path": "frontend/src/App.tsx", "type": "blob"},
            {"path": "backend/main.py", "type": "blob"},
            {"path": "backend/requirements.txt", "type": "blob"},
            {"path": "node_modules/x/index.js", "type": "blob"},
        ],
        file_contents={
            "backend/main.py": "from fastapi import FastAPI\napp = FastAPI()\n",
        },
        package_manifests={
            "frontend/package.json": '{"dependencies":{"react":"18.3.0"}}',
            "backend/requirements.txt": "fastapi\n",
        },
    )
    facts = build_code_facts(snapshot)
    public = facts.to_public_dict()

    assert set(public) == {
        "repository",
        "filtered_files",
        "languages",
        "manifests",
        "inventory",
        "source_facts",
        "call_graph",
        "data_flow",
    }
    assert "node_count" in public["call_graph"]
    assert "node_count" in public["data_flow"]
    assert "ast" not in public
    assert public["source_facts"]["counts"].get("import", 0) >= 1
    assert public["source_facts"]["counts"].get("function_call", 0) >= 1
    assert all("lineno" not in f for f in public["source_facts"]["facts"])
    assert public["repository"]["full_name"] == "o/r"
    assert public["repository"]["commit_sha"] == "abc123"
    assert "frontend/src/App.tsx" in public["filtered_files"]["paths"]
    assert "node_modules/x/index.js" not in public["filtered_files"]["paths"]
    assert public["languages"].get("typescript") == 1
    assert "react" in public["manifests"]["npm"]
    assert "fastapi" in public["manifests"]["python"]
    assert public["inventory"]["layout"] == "fullstack"
    assert facts.summary()["layout"] == "fullstack"


def test_github_client_uses_shared_manifest_names():
    from app.github.client import MANIFEST_NAMES as client_names

    assert "package.json" in MANIFEST_NAMES
    assert client_names is MANIFEST_NAMES
