"""Phase 16: explainable analysis result. Uncertain findings stay uncertain."""

from __future__ import annotations

import asyncio

from app.analysis.evidence_aggregator import EvidenceAggregator, GENUINE_AI_INTEGRATION
from app.analysis.facts import build_code_facts
from app.analysis.report import build_analysis_result
from app.github.client import RepoRef, RepoSnapshot
from app.github.validation import RepoAccessInfo
from app.main import app
from app.metrics.ai_usage import AiUsageMetric
from app.metrics.base import MetricContext
from app.metrics.frontend_backend import FrontendBackendMetric
from app.schemas import AnalysisResult, JobResponse
from app.scoring.aggregator import build_gated_result

_WRAPPER = '''
from openai import OpenAI

def generate_ai(prompt):
    client = OpenAI()
    return client.responses.create(prompt)
'''


def _snapshot(files: dict[str, str], *, name: str = "r") -> RepoSnapshot:
    return RepoSnapshot(
        ref=RepoRef("o", name),
        tree=[{"path": path, "type": "blob"} for path in files],
        file_contents=files,
    )


def test_ai_summary_genuine_shape():
    source = _WRAPPER + '''
@app.post("/chat")
def chat(body):
    message = body.message
    prompt = "Answer: " + message
    result = generate_ai(prompt)
    return {"answer": result}
'''
    snapshot = _snapshot({"backend/chat.py": source})
    facts = build_code_facts(snapshot)
    metric = asyncio.run(
        AiUsageMetric().run(MetricContext(snapshot=snapshot, extras={"code_facts": facts}))
    )
    metrics = {
        "ai_usage": {
            **metric.data,
            "providers": ["gemini"],
            "status": metric.status,
        }
    }
    verdict = EvidenceAggregator().aggregate(metrics=metrics, code_facts=facts)
    assert verdict["ai_classification"] == GENUINE_AI_INTEGRATION
    report = build_analysis_result(
        access={"is_public": True},
        repository=facts.repository,
        metrics=metrics,
        scoring={"total_score": 4},
        verdict=verdict,
        code_facts=facts,
        analysis=facts.to_public_dict(),
    )
    ai = report["ai"]
    assert ai == {
        "detected": True,
        "providers": ["gemini"],
        "integration_level": 6,
        "classification": "genuine_ai_integration",
        "model_invocation_detected": True,
        "user_input_reaches_model": True,
        "model_output_used": True,
        "hardcoded_response_detected": False,
        "confidence": 0.95,
    }
    assert report["repo"] == report["repository"]
    assert report["verdict"]["ai_classification"] == GENUINE_AI_INTEGRATION
    assert "analysis" in report
    evidence = report["evidence"]
    dynamic = next(item for item in evidence if item["rule_id"] == "AI.DYNAMIC_INPUT")
    assert dynamic["id"].startswith("ev_")
    assert dynamic["file"] == "backend/chat.py"
    assert dynamic["symbol"] == "chat"
    assert isinstance(dynamic["line"], int)
    assert "Gemini" in dynamic["description"]
    parsed = AnalysisResult.model_validate(report)
    assert parsed.ai.classification == "genuine_ai_integration"
    assert parsed.evidence[0].rule_id


def test_unproven_input_is_not_claimed():
    source = _WRAPPER + '''
@app.post("/chat")
def chat():
    result = generate_ai("Say hello")
    return {"answer": result}
'''
    snapshot = _snapshot({"backend/app.py": source})
    facts = build_code_facts(snapshot)
    metric = asyncio.run(
        AiUsageMetric().run(MetricContext(snapshot=snapshot, extras={"code_facts": facts}))
    )
    report = build_analysis_result(
        access={},
        repository=facts.repository,
        metrics={"ai_usage": metric.data},
        verdict=EvidenceAggregator().aggregate(metrics={"ai_usage": metric.data}, code_facts=facts),
        code_facts=facts,
    )
    assert report["ai"]["model_invocation_detected"] is True
    assert report["ai"]["user_input_reaches_model"] is False
    assert report["ai"]["classification"] != "genuine_ai_integration"
    assert any("not statically proven" in note for note in report["limitations"])


def test_limitations_dynamic_import():
    snapshot = _snapshot(
        {
            "backend/loader.py": "def load(name):\n    return __import__(name)\n",
        }
    )
    facts = build_code_facts(snapshot)
    report = build_analysis_result(
        access={},
        repository=facts.repository,
        metrics={},
        code_facts=facts,
    )
    assert "Dynamic import could not be resolved" in report["limitations"]


def test_limitations_frontend_url_at_runtime():
    snapshot = _snapshot(
        {
            "backend/main.py": '@app.post("/api/chat")\ndef chat():\n    pass\n',
            "frontend/src/Chat.tsx": "fetch(url);\n",
        }
    )
    facts = build_code_facts(snapshot)
    metric = asyncio.run(
        FrontendBackendMetric().run(MetricContext(snapshot=snapshot, extras={"code_facts": facts}))
    )
    report = build_analysis_result(
        access={},
        repository=facts.repository,
        metrics={"frontend_backend": metric.data},
        code_facts=facts,
    )
    assert report["frontend_backend"]["connected"] == "unknown"
    assert "Frontend URL constructed at runtime" in report["limitations"]


def test_unknown_connectivity_is_not_coerced_to_false():
    report = build_analysis_result(
        access={},
        repository={"owner": "o", "name": "r"},
        metrics={
            "frontend_backend": {
                "connected": "unknown",
                "connections": [],
                "backend_routes": [],
                "frontend_requests": [{"dynamic": True}],
                "unmatched_frontend": [{"reason": "unresolved_url"}],
                "unmatched_backend": [],
                "method_mismatches": [],
            }
        },
    )
    assert report["frontend_backend"]["connected"] == "unknown"
    assert report["frontend_backend"]["unknown"] is True


def test_gated_result_keeps_legacy_keys_and_states_limits():
    access = RepoAccessInfo(
        owner="o",
        name="r",
        is_public=False,
        exists=False,
        default_branch=None,
        reason="repository_not_found_or_inaccessible",
    )
    result = build_gated_result(
        access,
        github_url="https://github.com/o/r",
        submission_context=None,
    )
    for key in (
        "access",
        "repository",
        "architecture",
        "frontend_backend",
        "ai",
        "agents",
        "solution_fit",
        "metrics",
        "scoring",
        "evidence",
        "limitations",
        "metadata",
        "repo",
    ):
        assert key in result
    assert result["metrics"] == {}
    assert result["ai"]["classification"] == "not_analyzed"
    assert result["agents"]["classification"] == "not_analyzed"
    assert result["solution_fit"]["implementation_matches_claim"] is None
    assert "not publicly accessible" in result["limitations"][0].lower()


def test_job_response_schema_and_openapi():
    schema = app.openapi()
    components = schema["components"]["schemas"]
    assert "AnalysisResult" in components
    assert "EvidenceRecord" in components
    assert "AiResultSummary" in components
    job = components["JobResponse"]
    result_schema = job["properties"]["result"]
    assert "AnalysisResult" in str(result_schema)
    parsed = JobResponse(
        job_id="job_1",
        status="succeeded",
        github_url="https://github.com/o/r",
        metrics_requested=["ai_usage"],
        result={
            "access": {},
            "repository": {"owner": "o"},
            "ai": {
                "detected": True,
                "providers": ["gemini"],
                "integration_level": 6,
                "classification": "genuine_ai_integration",
                "model_invocation_detected": True,
                "user_input_reaches_model": True,
                "model_output_used": True,
                "hardcoded_response_detected": False,
                "confidence": 0.95,
            },
            "evidence": [
                {
                    "id": "ev_123",
                    "rule_id": "AI.DYNAMIC_INPUT",
                    "file": "backend/chat.py",
                    "line": 32,
                    "symbol": "chat",
                    "description": "Request message reaches Gemini model invocation",
                }
            ],
            "limitations": ["Dynamic import could not be resolved"],
        },
    )
    assert parsed.result is not None
    assert parsed.result.ai.integration_level == 6
    assert parsed.result.evidence[0].rule_id == "AI.DYNAMIC_INPUT"
    assert parsed.result.limitations == ["Dynamic import could not be resolved"]
