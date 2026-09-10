"""Phase 6: basic CALLS graph from normalized Code Facts."""

from __future__ import annotations

from app.analysis.call_graph import build_call_graph
from app.analysis.extract import extract_source_facts
from app.analysis.facts import build_code_facts
from app.github.client import RepoRef, RepoSnapshot


def _graph(files: dict[str, str]):
    facts, warnings = extract_source_facts(files)
    return build_call_graph(facts), facts, warnings


def test_chat_build_prompt_call_ai_path():
    graph, _facts, warnings = _graph(
        {
            "backend/routes/chat.py": '''
@app.post("/api/chat")
def chat():
    return build_prompt("hi")

def build_prompt(text):
    return call_ai(text)

def call_ai(prompt):
    return client.responses.create(prompt)
'''
        }
    )
    assert warnings == []
    assert graph.path_exists("chat", "call_ai")
    paths = graph.find_paths("chat", "call_ai")
    assert paths
    names = [[graph.graph.nodes[n]["name"] for n in path] for path in paths]
    assert any(row == ["chat", "build_prompt", "call_ai"] for row in names)
    assert "backend.routes.chat.build_prompt" in graph.direct_callees("chat")
    assert graph.direct_callers("build_prompt")
    assert graph.path_exists("call_ai", "client.responses.create")
    edge = next(
        e
        for e in graph.edges()
        if e["target"] == "client.responses.create"
    )
    assert edge["type"] == "CALLS"
    assert edge["confidence"] >= 0.85
    chat_id = graph.resolve("chat")
    assert graph.graph.nodes[chat_id]["kind"] == "route_handler"


def test_duplicate_function_names_stay_in_file():
    graph, _facts, _warnings = _graph(
        {
            "backend/a.py": '''
def helper():
    return 1

def run():
    return helper()
''',
            "backend/b.py": '''
def helper():
    return 2
''',
        }
    )
    a_helper = graph.resolve("backend.a.helper")
    b_helper = graph.resolve("backend.b.helper")
    assert a_helper != b_helper
    assert graph.resolve("helper") is None
    assert graph.direct_callees("run") == [a_helper]
    assert b_helper not in graph.direct_callees("run")
    assert not graph.path_exists("run", "backend.b.helper")


def test_imported_function_resolves():
    graph, _facts, _warnings = _graph(
        {
            "backend/services/ai.py": '''
def generate_answer(prompt):
    return client.responses.create(prompt)
''',
            "backend/routes/chat.py": '''
from backend.services.ai import generate_answer

@app.post("/api/chat")
def chat():
    return generate_answer("hi")
''',
        }
    )
    assert graph.path_exists("chat", "generate_answer")
    target = graph.resolve("generate_answer")
    assert target == "backend.services.ai.generate_answer"
    edge = next(e for e in graph.edges() if e["source"].endswith(".chat") and e["target"] == target)
    assert edge["type"] == "CALLS"
    assert edge["confidence"] == 0.94
    assert graph.path_exists("chat", "client.responses.create")


def test_unresolved_dynamic_call():
    graph, _facts, _warnings = _graph(
        {
            "backend/routes/chat.py": '''
def chat(handler):
    return handler("hi")
'''
        }
    )
    callees = graph.direct_callees("chat")
    assert any(c.startswith("unresolved:") for c in callees)
    edge = next(e for e in graph.edges() if e["target"].startswith("unresolved:"))
    assert edge["confidence"] <= 0.4
    assert graph.graph.nodes[edge["target"]]["kind"] == "unresolved"
    assert graph.resolve("helper") is None


def test_syntax_error_file_is_ignored():
    graph, _facts, warnings = _graph(
        {
            "backend/good.py": '''
def chat():
    return build_prompt()

def build_prompt():
    return 1
''',
            "backend/bad.py": "def broken(\n",
        }
    )
    assert any(w.get("file") == "backend/bad.py" and w.get("kind") == "syntax_error" for w in warnings)
    assert graph.path_exists("chat", "build_prompt")
    assert graph.resolve("broken") is None
    assert all(graph.graph.nodes[n].get("file") != "backend/bad.py" for n in graph.graph.nodes)


def test_build_code_facts_includes_call_graph():
    snapshot = RepoSnapshot(
        ref=RepoRef("o", "r"),
        tree=[
            {"path": "backend/routes/chat.py", "type": "blob"},
        ],
        file_contents={
            "backend/routes/chat.py": "def chat():\n    return build_prompt()\n\ndef build_prompt():\n    return 1\n",
        },
    )
    facts = build_code_facts(snapshot)
    assert facts.call_graph.path_exists("chat", "build_prompt")
    public = facts.to_public_dict()["call_graph"]
    assert public["edge_count"] >= 1
    assert public["edges"][0]["type"] == "CALLS"
