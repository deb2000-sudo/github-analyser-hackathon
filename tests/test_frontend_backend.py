"""Phase 5: frontend ↔ backend HTTP matching from Phase 4 output."""

from __future__ import annotations

import asyncio

from app.analysis.extract import extract_source_facts
from app.analysis.http_connect import match_frontend_backend
from app.analysis.http_io import detect_http_io
from app.github.client import RepoRef, RepoSnapshot
from app.metrics.base import MetricContext
from app.metrics.frontend_backend import FrontendBackendMetric


def _match_sources(files: dict[str, str]) -> dict:
    facts, _ = extract_source_facts(files)
    io = detect_http_io(facts)
    result = match_frontend_backend(io["backend_routes"], io["frontend_requests"])
    result["backend_routes"] = io["backend_routes"]
    result["frontend_requests"] = io["frontend_requests"]
    return result


def test_post_chat_matches_post_chat():
    result = _match_sources(
        {
            "backend/routes/chat.py": '''
@app.post("/api/chat")
def chat():
    return {"ok": True}
''',
            "frontend/src/Chat.tsx": '''
fetch("/api/chat", { method: "POST" });
''',
        }
    )
    assert result["connected"] is True
    assert result["connections"]
    conn = result["connections"][0]
    assert conn["method"] == "POST"
    assert conn["path"] == "/api/chat"
    assert conn["frontend_file"] == "frontend/src/Chat.tsx"
    assert conn["backend_file"] == "backend/routes/chat.py"
    assert isinstance(conn["frontend_line"], int)
    assert isinstance(conn["backend_line"], int)
    assert conn["confidence"] >= 0.97


def test_post_vs_get_same_path_not_matched():
    result = _match_sources(
        {
            "backend/routes/chat.py": '''
@app.get("/api/chat")
def chat():
    return {"ok": True}
''',
            "frontend/src/Chat.tsx": '''
fetch("/api/chat", { method: "POST" });
''',
        }
    )
    assert result["connected"] is False
    assert result["connections"] == []
    assert result["method_mismatches"]
    assert result["method_mismatches"][0]["frontend_method"] == "POST"
    assert result["method_mismatches"][0]["backend_method"] == "GET"
    assert result["method_mismatches"][0]["path"] == "/api/chat"


def test_post_chat_vs_post_ask_not_matched():
    result = _match_sources(
        {
            "backend/routes/chat.py": '''
@app.post("/api/ask")
def ask():
    return {"ok": True}
''',
            "frontend/src/Chat.tsx": '''
axios.post("/api/chat");
''',
        }
    )
    assert result["connected"] is False
    assert result["connections"] == []
    assert any(u["path"] == "/api/chat" and u["reason"] == "no_backend_match" for u in result["unmatched_frontend"])
    assert any(u["path"] == "/api/ask" and u["reason"] == "never_called" for u in result["unmatched_backend"])


def test_dynamic_base_url_matches_with_reduced_confidence():
    result = _match_sources(
        {
            "backend/main.py": '''
@app.post("/api/chat")
def chat():
    pass
''',
            "frontend/src/Chat.tsx": "fetch(`${API_URL}/api/chat`, { method: 'POST' });\n",
        }
    )
    assert result["connected"] is True
    conn = result["connections"][0]
    assert conn["method"] == "POST"
    assert conn["path"] == "/api/chat"
    assert conn["confidence"] < 0.97
    assert conn["confidence"] >= 0.85


def test_completely_dynamic_url_is_unknown():
    result = _match_sources(
        {
            "backend/main.py": '''
@app.post("/api/chat")
def chat():
    pass
''',
            "frontend/src/Chat.tsx": "fetch(url);\n",
        }
    )
    assert result["connected"] == "unknown"
    assert result["connections"] == []
    assert any(u["reason"] == "unresolved_url" for u in result["unmatched_frontend"])


def test_frontend_backend_metric_uses_code_facts():
    snapshot = RepoSnapshot(
        ref=RepoRef("o", "r"),
        tree=[
            {"path": "backend/main.py", "type": "blob"},
            {"path": "frontend/src/Chat.tsx", "type": "blob"},
        ],
        file_contents={
            "backend/main.py": '@app.post("/api/chat")\ndef chat():\n    return {}\n',
            "frontend/src/Chat.tsx": 'axios.post("/api/chat");\n',
        },
    )
    from app.analysis.facts import build_code_facts

    facts = build_code_facts(snapshot)
    result = asyncio.run(
        FrontendBackendMetric().run(MetricContext(snapshot=snapshot, extras={"code_facts": facts}))
    )
    assert result.status == "ok"
    assert result.data["connected"] is True
    assert result.data["connections"][0]["path"] == "/api/chat"
    assert result.data["backend_routes"]
    assert result.data["frontend_requests"]
