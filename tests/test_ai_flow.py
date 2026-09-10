"""Phase 9: user input → model and model output → application sink."""

from __future__ import annotations

import asyncio

from app.analysis.facts import build_code_facts
from app.github.client import RepoRef, RepoSnapshot
from app.metrics.ai_usage import AiUsageMetric
from app.metrics.base import MetricContext

_WRAPPER = '''
from openai import OpenAI

def generate_ai(prompt):
    client = OpenAI()
    return client.responses.create(prompt)
'''


def _run(source: str):
    snapshot = RepoSnapshot(
        ref=RepoRef("o", "r"),
        tree=[{"path": "backend/app.py", "type": "blob"}],
        file_contents={"backend/app.py": _WRAPPER + source},
    )
    extras = {"code_facts": build_code_facts(snapshot), "skip_file_fetch": True}
    return asyncio.run(AiUsageMetric().run(MetricContext(snapshot=snapshot, extras=extras)))


def _flow(result) -> dict:
    data = result.data
    verification = data["ai_verification"]
    assert verification["model_invocation_detected"] is data["model_invocation_detected"]
    return data


def test_case1_user_input_and_output_used():
    result = _run(
        '''
@app.post("/chat")
def chat(body):
    message = body.message
    prompt = "Answer: " + message
    result = generate_ai(prompt)
    return {"answer": result}
'''
    )
    data = _flow(result)
    assert data["model_invocation_detected"] is True
    assert data["user_input_reaches_model"] is True
    assert data["model_output_used"] is True
    assert data["ai_verification"]["confidence"] == 0.95
    assert data["input_flow_evidence"]
    assert data["output_flow_evidence"]
    input_path = data["input_flow_evidence"][0]["path"]
    assert "body.message" in input_path
    assert any("responses.create" in str(step) for step in input_path)
    output_path = data["output_flow_evidence"][0]["path"]
    assert any(str(step).startswith("ai_output:") or "responses.create" in str(step) for step in output_path)
    assert data["output_flow_evidence"][0]["sink"] == "http_response"


def test_case2_literal_input_output_used():
    result = _run(
        '''
@app.post("/chat")
def chat():
    result = generate_ai("Say hello")
    return {"answer": result}
'''
    )
    data = _flow(result)
    assert data["model_invocation_detected"] is True
    assert data["user_input_reaches_model"] is False
    assert data["model_output_used"] is True
    assert data["output_flow_evidence"]
    assert data["input_flow_evidence"] == []


def test_case3_user_input_output_discarded():
    result = _run(
        '''
@app.post("/chat")
def chat(body):
    result = generate_ai(body.message)
    return {"answer": "Thanks!"}
'''
    )
    data = _flow(result)
    assert data["model_invocation_detected"] is True
    assert data["user_input_reaches_model"] is True
    assert data["model_output_used"] is False
    assert data["input_flow_evidence"]
    assert data["output_flow_evidence"] == []


def test_case4_system_prompt_plus_user_input():
    result = _run(
        '''
SYSTEM_PROMPT = "You are helpful"

@app.post("/chat")
def chat(body):
    prompt = SYSTEM_PROMPT + body.message
    result = generate_ai(prompt)
    return result
'''
    )
    data = _flow(result)
    assert data["model_invocation_detected"] is True
    assert data["user_input_reaches_model"] is True
    assert data["model_output_used"] is True
    assert data["ai_verification"]["confidence"] == 0.95
    assert any("body.message" in row["path"] for row in data["input_flow_evidence"])


def test_no_invocation_has_no_flow():
    snapshot = RepoSnapshot(
        ref=RepoRef("o", "r"),
        tree=[{"path": "backend/app.py", "type": "blob"}],
        file_contents={
            "backend/app.py": (
                "@app.post('/chat')\n"
                "def chat(body):\n"
                "    return {'answer': body.message}\n"
            )
        },
    )
    extras = {"code_facts": build_code_facts(snapshot), "skip_file_fetch": True}
    result = asyncio.run(AiUsageMetric().run(MetricContext(snapshot=snapshot, extras=extras)))
    data = result.data
    assert data["model_invocation_detected"] is False
    assert data["user_input_reaches_model"] is False
    assert data["model_output_used"] is False
    assert data["input_flow_evidence"] == []
    assert data["output_flow_evidence"] == []
    assert data["ai_verification"]["confidence"] == 0.9
