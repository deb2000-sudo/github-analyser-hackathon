"""Phase 3: Python / JS / TS parse into comparable normalized Code Facts.

No call graph. No data flow. No Gemini.
"""

from __future__ import annotations

from app.analysis.extract import extract_file_facts, extract_source_facts
from app.analysis.facts import build_code_facts
from app.github.client import RepoRef, RepoSnapshot

PY_SAMPLE = '''
from openai import OpenAI
import os

class Client:
    def generate_answer(self, prompt):
        client = OpenAI()
        return client.responses.create(prompt)

@app.get("/items")
def list_items():
    key = os.getenv("API_KEY")
    return requests.get("https://example.com")
'''

TS_SAMPLE = '''
import { OpenAI } from "openai";

class Client {
  generateAnswer(prompt: string) {
    const client = new OpenAI();
    return client.responses.create(prompt);
  }
}

app.get("/items", function listItems() {
  const key = process.env.API_KEY;
  return fetch("https://example.com");
});
'''

JS_SAMPLE = '''
const { OpenAI } = require("openai");

class Client {
  generateAnswer(prompt) {
    const client = new OpenAI();
    return client.responses.create(prompt);
  }
}

app.get("/items", function listItems() {
  const key = process.env.API_KEY;
  return fetch("https://example.com");
});
'''


def _by_type(facts: list[dict], fact_type: str) -> list[dict]:
    return [f for f in facts if f.get("fact_type") == fact_type]


def _assert_plain_dicts(facts: list[dict]) -> None:
    for fact in facts:
        assert isinstance(fact, dict)
        assert "fact_type" in fact
        assert "file" in fact
        assert isinstance(fact.get("line"), int)
        for value in fact.values():
            assert not hasattr(value, "lineno") or isinstance(value, int)
            assert type(value).__name__ not in {"FunctionDef", "Node", "Tree"}


def test_python_extracts_core_facts():
    facts, warnings = extract_file_facts("backend/services/ai.py", PY_SAMPLE)
    assert warnings == []
    _assert_plain_dicts(facts)

    imports = _by_type(facts, "import")
    assert any(f["module"] == "openai" and "OpenAI" in f["names"] for f in imports)
    assert any(f["module"] == "os" for f in imports)

    functions = _by_type(facts, "function")
    generate = next(f for f in functions if f["name"] == "generate_answer")
    assert generate["file"] == "backend/services/ai.py"
    assert generate["line"] >= 1
    assert "prompt" in generate["parameters"]
    assert generate["scope"] == "Client"
    list_items = next(f for f in functions if f["name"] == "list_items")
    assert "app.get" in list_items["decorators"]

    classes = _by_type(facts, "class")
    assert any(f["name"] == "Client" for f in classes)

    calls = _by_type(facts, "function_call")
    create = next(f for f in calls if f["callee"] == "client.responses.create")
    assert create["scope"] == "generate_answer"
    assert create["arguments"] == ["prompt"]
    assert create["line"] >= 1

    assigns = _by_type(facts, "assignment")
    assert any(f["target"] == "client" and f["scope"] == "generate_answer" for f in assigns)

    returns = _by_type(facts, "return")
    assert any(f["scope"] == "generate_answer" and "client.responses.create" in (f["value"] or "") for f in returns)

    attrs = _by_type(facts, "attribute_access")
    assert any(f["object"] == "client.responses" and f["attribute"] == "create" for f in attrs)

    routes = _by_type(facts, "route")
    assert any(f["method"] == "GET" and f["path"] == "/items" and f["handler"] == "list_items" for f in routes)

    envs = _by_type(facts, "environment_variable")
    assert any(f["name"] == "API_KEY" for f in envs)

    http = _by_type(facts, "http_request")
    assert any(f["callee"] == "requests.get" and f["url"] == "https://example.com" for f in http)


def test_javascript_extracts_core_facts():
    facts, warnings = extract_file_facts("frontend/services/ai.js", JS_SAMPLE)
    assert warnings == []
    _assert_plain_dicts(facts)
    _assert_js_family(facts, language="javascript")


def test_typescript_extracts_core_facts():
    facts, warnings = extract_file_facts("frontend/services/ai.ts", TS_SAMPLE)
    assert warnings == []
    _assert_plain_dicts(facts)
    _assert_js_family(facts, language="typescript")
    generate = next(f for f in _by_type(facts, "function") if f["name"] == "generateAnswer")
    assert generate["parameters"] == ["prompt"]


def _assert_js_family(facts: list[dict], *, language: str) -> None:
    assert all(f.get("language") == language for f in facts)

    imports = _by_type(facts, "import")
    assert any(f["module"] == "openai" for f in imports)

    classes = _by_type(facts, "class")
    assert any(f["name"] == "Client" for f in classes)

    functions = _by_type(facts, "function")
    generate = next(f for f in functions if f["name"] == "generateAnswer")
    assert "prompt" in generate["parameters"]
    assert generate["scope"] == "Client"

    calls = _by_type(facts, "function_call")
    create = next(f for f in calls if f["callee"] == "client.responses.create")
    assert create["scope"] == "generateAnswer"
    assert create["arguments"] == ["prompt"]

    assigns = _by_type(facts, "assignment")
    assert any(f["target"] == "client" and f["scope"] == "generateAnswer" for f in assigns)

    returns = _by_type(facts, "return")
    assert any(f["scope"] == "generateAnswer" for f in returns)

    attrs = _by_type(facts, "attribute_access")
    assert any(f["object"] == "client.responses" and f["attribute"] == "create" for f in attrs)

    routes = _by_type(facts, "route")
    assert any(f["method"] == "GET" and f["path"] == "/items" for f in routes)

    envs = _by_type(facts, "environment_variable")
    assert any(f["name"] == "API_KEY" and f["accessor"] == "process.env" for f in envs)

    http = _by_type(facts, "http_request")
    assert any(f["callee"] == "fetch" and f["url"] == "https://example.com" for f in http)


def test_python_and_typescript_produce_comparable_facts():
    py_facts, py_warn = extract_file_facts("backend/services/ai.py", PY_SAMPLE)
    ts_facts, ts_warn = extract_file_facts("frontend/services/ai.ts", TS_SAMPLE)
    assert py_warn == [] and ts_warn == []

    py_types = {f["fact_type"] for f in py_facts}
    ts_types = {f["fact_type"] for f in ts_facts}
    shared = {
        "import",
        "class",
        "function",
        "function_call",
        "assignment",
        "return",
        "attribute_access",
        "route",
        "environment_variable",
        "http_request",
    }
    assert shared <= py_types
    assert shared <= ts_types

    py_call = next(f for f in py_facts if f["fact_type"] == "function_call" and f["callee"] == "client.responses.create")
    ts_call = next(f for f in ts_facts if f["fact_type"] == "function_call" and f["callee"] == "client.responses.create")
    assert py_call["arguments"] == ts_call["arguments"] == ["prompt"]
    assert py_call["callee"] == ts_call["callee"]

    py_fn = next(f for f in py_facts if f["fact_type"] == "function" and f["name"] == "generate_answer")
    ts_fn = next(f for f in ts_facts if f["fact_type"] == "function" and f["name"] == "generateAnswer")
    assert [p for p in py_fn["parameters"] if p != "self"] == ts_fn["parameters"]
    assert py_fn["scope"] == ts_fn["scope"] == "Client"

    py_class = next(f for f in py_facts if f["fact_type"] == "class")
    ts_class = next(f for f in ts_facts if f["fact_type"] == "class")
    assert py_class["name"] == ts_class["name"] == "Client"

    py_import = next(f for f in py_facts if f["fact_type"] == "import" and f["module"] == "openai")
    ts_import = next(f for f in ts_facts if f["fact_type"] == "import" and f["module"] == "openai")
    assert py_import["module"] == ts_import["module"]

    py_route = next(f for f in py_facts if f["fact_type"] == "route")
    ts_route = next(f for f in ts_facts if f["fact_type"] == "route")
    assert py_route["method"] == ts_route["method"] == "GET"
    assert py_route["path"] == ts_route["path"] == "/items"

    py_env = next(f for f in py_facts if f["fact_type"] == "environment_variable")
    ts_env = next(f for f in ts_facts if f["fact_type"] == "environment_variable")
    assert py_env["name"] == ts_env["name"] == "API_KEY"

    py_http = next(f for f in py_facts if f["fact_type"] == "http_request")
    ts_http = next(f for f in ts_facts if f["fact_type"] == "http_request")
    assert py_http["url"] == ts_http["url"] == "https://example.com"


def test_syntax_error_warns_and_does_not_fail_repo():
    facts, warnings = extract_source_facts(
        {
            "good.py": "def ok():\n    return 1\n",
            "bad.py": "def broken(\n",
            "ok.ts": "export function ping(x: string) { return x; }\n",
            "bad.js": "function nope(\n",
        }
    )
    assert any(f["fact_type"] == "function" and f["name"] == "ok" and f["file"] == "good.py" for f in facts)
    assert any(f["fact_type"] == "function" and f["name"] == "ping" and f["file"] == "ok.ts" for f in facts)
    kinds = {w["file"]: w["kind"] for w in warnings}
    assert kinds.get("bad.py") == "syntax_error"
    assert kinds.get("bad.js") == "syntax_error"
    assert "good.py" not in kinds
    assert "ok.ts" not in kinds


def test_build_code_facts_attaches_normalized_source_facts():
    snapshot = RepoSnapshot(
        ref=RepoRef("o", "r"),
        tree=[
            {"path": "backend/services/ai.py", "type": "blob"},
            {"path": "frontend/services/ai.ts", "type": "blob"},
        ],
        file_contents={
            "backend/services/ai.py": PY_SAMPLE,
            "frontend/services/ai.ts": TS_SAMPLE,
        },
    )
    code_facts = build_code_facts(snapshot)
    types = {f["fact_type"] for f in code_facts.source_facts}
    assert "function_call" in types
    assert code_facts.parse_warnings == []
    public = code_facts.to_public_dict()["source_facts"]
    assert public["fact_count"] == len(code_facts.source_facts)
    assert public["counts"]["function"] >= 2
