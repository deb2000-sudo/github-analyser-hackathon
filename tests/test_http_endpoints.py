"""Phase 4: backend routes and frontend HTTP clients from Code Facts."""

from __future__ import annotations

from app.analysis.extract import extract_file_facts, extract_source_facts
from app.analysis.http_io import detect_http_io, normalize_url


def _io_from_source(path: str, source: str) -> dict:
    facts, warnings = extract_file_facts(path, source)
    assert warnings == [] or all(w.get("kind") != "parse_error" for w in warnings)
    return detect_http_io(facts)


def test_fastapi_routes():
    source = '''
from fastapi import FastAPI
app = FastAPI()

@app.get("/health")
def health():
    return {"ok": True}

@app.post("/api/chat")
async def chat():
    return {"msg": "hi"}

@app.put("/api/chat/{id}")
def update():
    pass

@app.delete("/api/chat/{id}")
def remove():
    pass
'''
    io = _io_from_source("backend/routes/chat.py", source)
    routes = {(r["method"], r["path"]) for r in io["backend_routes"]}
    assert ("GET", "/health") in routes
    assert ("POST", "/api/chat") in routes
    assert ("PUT", "/api/chat/{id}") in routes
    assert ("DELETE", "/api/chat/{id}") in routes
    post = next(r for r in io["backend_routes"] if r["method"] == "POST")
    assert post["type"] == "http_route"
    assert post["file"] == "backend/routes/chat.py"
    assert isinstance(post["line"], int)


def test_flask_route():
    source = '''
from flask import Flask
app = Flask(__name__)

@app.route("/api/chat", methods=["POST"])
def chat():
    return "ok"
'''
    io = _io_from_source("backend/app.py", source)
    routes = io["backend_routes"]
    assert any(r["method"] == "POST" and r["path"] == "/api/chat" for r in routes)
    assert routes[0]["type"] == "http_route"


def test_express_routes():
    source = '''
const express = require("express");
const app = express();
const router = express.Router();

app.get("/api/items", listItems);
router.post("/api/chat", chat);
'''
    io = _io_from_source("backend/server.js", source)
    routes = {(r["method"], r["path"]) for r in io["backend_routes"]}
    assert ("GET", "/api/items") in routes
    assert ("POST", "/api/chat") in routes
    assert not any(r.get("callee", "").startswith("axios") for r in io["backend_routes"])


def test_nextjs_api_route_handler():
    source = '''
export async function POST() {
  return Response.json({ ok: true });
}
export async function GET() {
  return Response.json({ ok: true });
}
'''
    io = _io_from_source("app/api/chat/route.ts", source)
    routes = {(r["method"], r["path"]) for r in io["backend_routes"]}
    assert ("POST", "/api/chat") in routes
    assert ("GET", "/api/chat") in routes


def test_fetch_client():
    source = '''
fetch("/api/chat", { method: "POST" });
fetch("http://localhost:8000/api/chat");
'''
    io = _io_from_source("frontend/src/Chat.tsx", source)
    clients = io["frontend_requests"]
    assert any(
        c["type"] == "http_client" and c["method"] == "POST" and c["path"] == "/api/chat"
        for c in clients
    )
    assert any(
        c["method"] == "GET" and c["path"] == "/api/chat" and "localhost" in str(c.get("raw") or "")
        for c in clients
    )


def test_axios_clients():
    source = '''
axios.get("/api/items");
axios.post("/api/chat");
axios.put("/api/chat");
axios.delete("/api/chat");
'''
    io = _io_from_source("frontend/src/api.ts", source)
    pairs = {(c["method"], c["path"]) for c in io["frontend_requests"]}
    assert pairs >= {("GET", "/api/items"), ("POST", "/api/chat"), ("PUT", "/api/chat"), ("DELETE", "/api/chat")}
    assert io["backend_routes"] == []


def test_dynamic_base_url():
    source = "fetch(`${API_URL}/api/chat`, { method: 'POST' });\n"
    io = _io_from_source("frontend/src/Chat.tsx", source)
    client = io["frontend_requests"][0]
    assert client["path"] == "/api/chat"
    assert client["method"] == "POST"
    assert client["resolved"] is True
    assert client["dynamic_base"] is True
    info = normalize_url("${API_URL}/api/chat")
    assert info["path"] == "/api/chat"
    assert info["dynamic_base"] is True
    assert info["resolved"] is True


def test_unknown_url_variable():
    source = "fetch(url);\n"
    io = _io_from_source("frontend/src/Chat.tsx", source)
    client = io["frontend_requests"][0]
    assert client["type"] == "http_client"
    assert client["path"] is None
    assert client["resolved"] is False
    assert client["dynamic"] is True
    info = normalize_url("endpoint")
    assert info["resolved"] is False
    assert info["path"] is None


def test_axios_is_not_a_backend_route():
    facts, _ = extract_source_facts(
        {"frontend/src/api.ts": 'axios.post("/api/chat");\n'}
    )
    io = detect_http_io(facts)
    assert io["backend_routes"] == []
    assert io["frontend_requests"][0]["method"] == "POST"
