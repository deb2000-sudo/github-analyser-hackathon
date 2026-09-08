"""Phase 2: deterministic fullstack classification from FileInventory evidence.

No AST. No Gemini. Directory names alone must not classify the app.
"""

from __future__ import annotations

import asyncio
import json

from app.analysis.facts import build_code_facts
from app.analysis.inventory import FileInventory
from app.github.client import RepoRef, RepoSnapshot
from app.metrics.base import MetricContext
from app.metrics.fullstack import FullstackMetric


def _tree(*paths: str) -> list[dict[str, str]]:
    return [{"path": p, "type": "blob"} for p in paths]


def _run(
    tree: list[dict[str, str]],
    *,
    manifests: dict[str, str] | None = None,
    contents: dict[str, str] | None = None,
    use_inventory: bool = True,
):
    snapshot = RepoSnapshot(
        ref=RepoRef("o", "r"),
        tree=tree,
        package_manifests=manifests or {},
        file_contents=contents or {},
    )
    extras: dict = {}
    if use_inventory:
        extras["code_facts"] = build_code_facts(snapshot)
    return asyncio.run(FullstackMetric().run(MetricContext(snapshot=snapshot, extras=extras)))


def test_react_only_is_frontend():
    result = _run(
        _tree("frontend/package.json", "frontend/src/App.tsx", "frontend/public/index.html"),
        manifests={"frontend/package.json": json.dumps({"dependencies": {"react": "18.3.0"}})},
    )
    data = result.data
    assert data["application_type"] == "frontend"
    assert data["frontend"] == {"detected": True, "framework": "react"}
    assert data["backend"]["detected"] is False
    assert data["backend"]["framework"] is None
    react_signals = [s for s in data["signals"] if s["rule_id"] == "FRAMEWORK.REACT.DEPENDENCY"]
    assert react_signals
    assert react_signals[0]["category"] == "framework"
    assert react_signals[0]["value"] == "react"
    assert react_signals[0]["confidence"] >= 0.9
    assert react_signals[0]["evidence"][0]["file"] == "frontend/package.json"


def test_fastapi_only_is_backend():
    result = _run(
        _tree("requirements.txt", "main.py"),
        manifests={"requirements.txt": "fastapi==0.115.0\nuvicorn\n"},
        contents={"main.py": "from fastapi import FastAPI\napp = FastAPI()\n"},
    )
    data = result.data
    assert data["application_type"] == "backend"
    assert data["backend"] == {"detected": True, "framework": "fastapi"}
    assert data["frontend"]["detected"] is False


def test_react_plus_fastapi_is_full_stack():
    result = _run(
        _tree(
            "frontend/package.json",
            "frontend/src/App.tsx",
            "backend/requirements.txt",
            "backend/main.py",
        ),
        manifests={
            "frontend/package.json": json.dumps({"dependencies": {"react": "18.3.0"}}),
            "backend/requirements.txt": "fastapi\n",
        },
        contents={"backend/main.py": "from fastapi import FastAPI\napp = FastAPI()\n"},
    )
    data = result.data
    assert data["application_type"] == "full_stack"
    assert data["confidence"] >= 0.9
    assert data["frontend"] == {"detected": True, "framework": "react"}
    assert data["backend"] == {"detected": True, "framework": "fastapi"}
    assert "package.json" in data["detected_manifests"]
    assert "requirements.txt" in data["detected_manifests"]


def test_readme_claims_react_without_dependency_or_code():
    result = _run(
        _tree("README.md", "notes.txt"),
        contents={"README.md": "# Demo\n\nBuilt with React and a modern UI.\n"},
    )
    data = result.data
    assert data["frontend"]["detected"] is False
    assert data["frontend"]["framework"] is None
    assert data["application_type"] in {"unknown", "library", "cli"}
    readme = [s for s in data["signals"] if s["rule_id"] == "FRAMEWORK.REACT.README"]
    assert readme
    assert readme[0]["confidence"] < 0.5
    assert readme[0]["value"] == "react"


def test_express_only_package_json_is_backend():
    result = _run(
        _tree("package.json", "server.js"),
        manifests={"package.json": json.dumps({"dependencies": {"express": "4.19.0"}})},
        contents={
            "server.js": "const express = require('express');\nconst app = express();\napp.listen(3000);\n"
        },
    )
    data = result.data
    assert data["application_type"] == "backend"
    assert data["backend"] == {"detected": True, "framework": "express"}
    assert data["frontend"]["detected"] is False


def test_nextjs_pages_only_is_frontend_not_backend():
    result = _run(
        _tree("package.json", "next.config.js", "app/page.tsx"),
        manifests={"package.json": json.dumps({"dependencies": {"next": "14.0.0", "react": "18.0.0"}})},
        contents={"app/page.tsx": "export default function Page() { return <main>Hi</main> }\n"},
    )
    data = result.data
    assert data["application_type"] == "frontend"
    assert data["frontend"]["detected"] is True
    assert data["frontend"]["framework"] == "nextjs"
    assert data["backend"]["detected"] is False
    assert data["backend"]["framework"] is None


def test_nextjs_api_route_distinguishes_backend():
    result = _run(
        _tree("package.json", "next.config.js", "app/page.tsx", "app/api/hello/route.ts"),
        manifests={"package.json": json.dumps({"dependencies": {"next": "14.0.0", "react": "18.0.0"}})},
        contents={
            "app/page.tsx": "export default function Page() { return <main>Hi</main> }\n",
            "app/api/hello/route.ts": "export async function GET() { return Response.json({ ok: true }) }\n",
        },
    )
    data = result.data
    assert data["application_type"] == "full_stack"
    assert data["frontend"]["framework"] == "nextjs"
    assert data["backend"]["detected"] is True
    assert data["backend"]["framework"] == "nextjs_api"


def test_directory_names_alone_do_not_classify():
    result = _run(
        _tree("frontend/index.html", "backend/utils.py", "frontend/src/styles.css"),
        contents={
            "frontend/index.html": "<html><body>static page</body></html>",
            "backend/utils.py": "def add(a, b):\n    return a + b\n",
        },
    )
    data = result.data
    assert data["frontend"]["detected"] is False
    assert data["backend"]["detected"] is False
    assert data["application_type"] in {"unknown", "library", "cli"}


def test_uses_phase1_file_inventory():
    snapshot = RepoSnapshot(
        ref=RepoRef("o", "r"),
        tree=_tree("package.json"),
        package_manifests={"package.json": json.dumps({"dependencies": {"react": "18.0.0"}})},
    )
    facts = build_code_facts(snapshot)
    assert isinstance(facts.file_inventory, FileInventory)
    assert facts.file_inventory.has_named("package.json")
    result = asyncio.run(
        FullstackMetric().run(MetricContext(snapshot=snapshot, extras={"code_facts": facts}))
    )
    assert result.data["application_type"] == "frontend"
    assert result.data["frontend"]["framework"] == "react"
