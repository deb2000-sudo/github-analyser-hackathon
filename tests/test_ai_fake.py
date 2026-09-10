"""Phase 10: fake-AI rules — positive and negative cases for every rule."""

from __future__ import annotations

import asyncio

from app.analysis.facts import build_code_facts
from app.github.client import RepoRef, RepoSnapshot
from app.metrics.ai_usage import AiUsageMetric
from app.metrics.base import MetricContext

_REAL_AI = '''
from openai import OpenAI

def generate_ai(prompt):
    client = OpenAI()
    return client.responses.create(prompt)
'''


def _run(files: dict[str, str], manifests: dict[str, str] | None = None):
    tree = [{"path": path, "type": "blob"} for path in {**files, **(manifests or {})}]
    snapshot = RepoSnapshot(
        ref=RepoRef("o", "r"),
        tree=tree,
        file_contents=files,
        package_manifests=manifests or {},
    )
    extras = {"code_facts": build_code_facts(snapshot), "skip_file_fetch": True}
    return asyncio.run(AiUsageMetric().run(MetricContext(snapshot=snapshot, extras=extras)))


def _failed(data: dict, rule_id: str) -> dict | None:
    for item in data.get("ai_findings") or []:
        if item.get("rule_id") == rule_id and item.get("status") == "failed":
            return item
    return None


def _warning(data: dict, rule_id: str) -> dict | None:
    for item in data.get("ai_findings") or []:
        if item.get("rule_id") == rule_id and item.get("status") == "warning":
            return item
    return None


def test_hardcoded_response_positive():
    result = _run(
        {
            "backend/chat.py": '''
@app.post("/chat")
def chat(body):
    return {"answer": "This is your AI answer"}
'''
        }
    )
    finding = _failed(result.data, "AI.HARDCODED_RESPONSE")
    assert finding is not None
    assert finding["confidence"] == 0.97
    assert finding["evidence"]
    assert finding["evidence"][0]["file"] == "backend/chat.py"
    assert isinstance(finding["evidence"][0]["line"], int)


def test_hardcoded_response_negative_system_prompt_is_genuine():
    result = _run(
        {
            "backend/chat.py": _REAL_AI
            + '''
SYSTEM_PROMPT = "You are a career mentor"

@app.post("/chat")
def chat(request):
    result = generate_ai(SYSTEM_PROMPT + request.message)
    return result
'''
        }
    )
    assert _failed(result.data, "AI.HARDCODED_RESPONSE") is None
    assert result.data["model_invocation_detected"] is True
    assert result.data["user_input_reaches_model"] is True
    assert result.data["model_output_used"] is True


def test_static_response_pool_positive():
    result = _run(
        {
            "backend/chat.py": '''
import random

@app.post("/chat")
def chat(body):
    responses = [
        "Great question",
        "Try again",
        "Interesting",
    ]
    return random.choice(responses)
'''
        }
    )
    finding = _failed(result.data, "AI.STATIC_RESPONSE_POOL")
    assert finding is not None
    assert finding["evidence"][0]["file"] == "backend/chat.py"


def test_static_response_pool_negative_pool_only_as_prompt():
    result = _run(
        {
            "backend/chat.py": _REAL_AI
            + '''
import random

@app.post("/chat")
def chat(body):
    styles = ["brief", "detailed", "friendly"]
    prompt = random.choice(styles) + body.message
    return generate_ai(prompt)
'''
        }
    )
    assert _failed(result.data, "AI.STATIC_RESPONSE_POOL") is None


def test_deterministic_transform_positive_upper():
    result = _run(
        {
            "backend/chat.py": '''
@app.post("/chat")
def chat(request):
    return request.message.upper()
'''
        }
    )
    finding = _failed(result.data, "AI.DETERMINISTIC_TEXT_TRANSFORMATION")
    assert finding is not None


def test_deterministic_transform_positive_concat():
    result = _run(
        {
            "backend/chat.py": '''
@app.post("/chat")
def chat(request):
    return "Improved: " + request.message
'''
        }
    )
    assert _failed(result.data, "AI.DETERMINISTIC_TEXT_TRANSFORMATION") is not None


def test_deterministic_transform_negative_concat_is_prompt_only():
    result = _run(
        {
            "backend/chat.py": _REAL_AI
            + '''
@app.post("/chat")
def chat(request):
    prompt = "Improved: " + request.message
    return generate_ai(prompt)
'''
        }
    )
    assert _failed(result.data, "AI.DETERMINISTIC_TEXT_TRANSFORMATION") is None
    assert result.data["model_output_used"] is True


def test_unused_ai_dependency_positive():
    result = _run(
        {"backend/app.py": "def main():\n    return 'ok'\n"},
        manifests={"requirements.txt": "openai==1.40.0\n"},
    )
    finding = _failed(result.data, "AI.UNUSED_AI_DEPENDENCY")
    assert finding is not None
    assert finding["evidence"][0]["file"] == "requirements.txt"


def test_unused_ai_dependency_negative_real_invocation():
    result = _run(
        {
            "backend/chat.py": _REAL_AI
            + '''
@app.post("/chat")
def chat(body):
    return generate_ai(body.message)
'''
        },
        manifests={"requirements.txt": "openai==1.40.0\n"},
    )
    assert _failed(result.data, "AI.UNUSED_AI_DEPENDENCY") is None


def test_mock_ai_positive_warning_only():
    result = _run(
        {
            "backend/chat.py": '''
def mock_ai(message):
    return "Hello from the model"

@app.post("/chat")
def chat(body):
    return mock_ai(body.message)
'''
        }
    )
    warning = _warning(result.data, "AI.MOCK_AI_IMPLEMENTATION")
    assert warning is not None
    assert warning["status"] == "warning"
    assert warning["confidence"] < 0.9
    assert _failed(result.data, "AI.MOCK_AI_IMPLEMENTATION") is None


def test_mock_ai_negative_name_does_not_prove_fake():
    result = _run(
        {
            "backend/chat.py": '''
from openai import OpenAI

def mock_openai(prompt):
    client = OpenAI()
    return client.responses.create(prompt)

@app.post("/chat")
def chat(body):
    return mock_openai(body.message)
'''
        }
    )
    assert _failed(result.data, "AI.MOCK_AI_IMPLEMENTATION") is None
    assert _warning(result.data, "AI.MOCK_AI_IMPLEMENTATION") is None
    assert result.data["model_invocation_detected"] is True
    assert result.data["model_output_used"] is True


def test_ai_output_discarded_positive():
    result = _run(
        {
            "backend/chat.py": _REAL_AI
            + '''
@app.post("/chat")
def chat(request):
    result = generate_ai(request.message)
    return "Thank you!"
'''
        }
    )
    finding = _failed(result.data, "AI.AI_OUTPUT_DISCARDED")
    assert finding is not None
    assert result.data["model_output_used"] is False


def test_ai_output_discarded_negative_output_returned():
    result = _run(
        {
            "backend/chat.py": _REAL_AI
            + '''
@app.post("/chat")
def chat(request):
    result = generate_ai(request.message)
    return {"answer": result}
'''
        }
    )
    assert _failed(result.data, "AI.AI_OUTPUT_DISCARDED") is None
    assert result.data["model_output_used"] is True


def test_fake_fallback_dominates_positive():
    result = _run(
        {
            "backend/chat.py": _REAL_AI
            + '''
FALLBACK = "Sorry, I cannot help right now"

@app.post("/chat")
def chat(body):
    result = generate_ai(body.message)
    return FALLBACK
'''
        }
    )
    finding = _failed(result.data, "AI.FAKE_FALLBACK_DOMINATES")
    assert finding is not None
    assert _failed(result.data, "AI.AI_OUTPUT_DISCARDED") is not None


def test_fake_fallback_dominates_negative_real_fallback_on_error_only():
    result = _run(
        {
            "backend/chat.py": _REAL_AI
            + '''
FALLBACK = "Sorry, I cannot help right now"

@app.post("/chat")
def chat(body):
    try:
        return generate_ai(body.message)
    except Exception:
        return FALLBACK
'''
        }
    )
    assert _failed(result.data, "AI.FAKE_FALLBACK_DOMINATES") is None
    assert result.data["model_output_used"] is True
