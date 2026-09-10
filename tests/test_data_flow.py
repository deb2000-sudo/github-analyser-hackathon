"""Phase 7: simple SOURCE → SINK value flow from Code Facts."""

from __future__ import annotations

from app.analysis.data_flow import DataFlowConfig, build_data_flow
from app.analysis.extract import extract_source_facts
from app.analysis.facts import build_code_facts
from app.github.client import RepoRef, RepoSnapshot


def _flow(source: str, *, config: DataFlowConfig | None = None):
    facts, warnings = extract_source_facts({"backend/app.py": source})
    assert all(w.get("kind") != "parse_error" for w in warnings)
    return build_data_flow(facts, config=config)


def test_case1_alias_reaches_send():
    dfg = _flow(
        '''
def handle(body):
    message = body.message
    x = message
    send(x)
'''
    )
    assert dfg.path_exists("body.message", "send") is True
    paths = dfg.find_paths("body.message", "send")
    assert paths


def test_case2_literal_does_not_reach_send():
    dfg = _flow(
        '''
def handle():
    send("hello")
'''
    )
    assert dfg.path_exists("body.message", "send") is False
    assert dfg.path_exists("source", "send") is False


def test_case3_fstring_reaches_send():
    dfg = _flow(
        '''
def handle(body):
    prompt = f"Question: {body.message}"
    send(prompt)
'''
    )
    assert dfg.path_exists("body.message", "send") is True


def test_case4_dynamic_call_is_unknown():
    dfg = _flow(
        '''
def handle(body):
    fn = getattr(mod, name)
    fn(body.message)
'''
    )
    assert dfg.path_exists("body.message", "fn") == "unknown"
    assert dfg.path_exists("body.message", "send") is False


def test_concatenation_reaches_send():
    dfg = _flow(
        '''
def handle(body):
    message = body.message
    prompt = "Answer: " + message
    send(prompt)
'''
    )
    assert dfg.path_exists("body.message", "send") is True


def test_return_and_dict_construction():
    dfg = _flow(
        '''
def generate_answer(prompt):
    return prompt

def handle(body):
    message = body.message
    result = generate_answer(message)
    return {"answer": result}
'''
    )
    assert dfg.path_exists("body.message", "prompt") is True
    assert dfg.path_exists("body.message", "result") is True
    assert dfg.path_exists("body.message", "<return>") is True
    assert dfg.path_exists("body.message", "send") is False


def test_configurable_sink():
    src = '''
def handle(body):
    message = body.message
    write_out(message)
'''
    default = _flow(src)
    assert default.path_exists("body.message", "write_out") is False
    custom = _flow(src, config=DataFlowConfig(sink_callees=("write_out",)))
    assert custom.path_exists("body.message", "write_out") is True


def test_build_code_facts_includes_data_flow():
    snapshot = RepoSnapshot(
        ref=RepoRef("o", "r"),
        tree=[{"path": "backend/app.py", "type": "blob"}],
        file_contents={
            "backend/app.py": "def handle(body):\n    message = body.message\n    send(message)\n",
        },
    )
    facts = build_code_facts(snapshot)
    assert facts.data_flow.path_exists("body.message", "send") is True
    public = facts.to_public_dict()["data_flow"]
    assert public["edge_count"] >= 1
