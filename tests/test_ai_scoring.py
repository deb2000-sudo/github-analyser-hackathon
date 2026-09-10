"""Phase 12: deterministic AI evidence drives the existing Uses an LLM rubric."""

from __future__ import annotations

import asyncio

from app.analysis.ai_fake import (
    RULE_DISCARDED,
    RULE_HARDCODED,
    RULE_MOCK,
    RULE_POOL,
    RULE_UNUSED_DEP,
)
from app.analysis.facts import build_code_facts
from app.github.client import RepoRef, RepoSnapshot
from app.github.validation import RepoAccessInfo
from app.metrics.ai_usage import AiUsageMetric
from app.metrics.base import MetricContext
from app.scoring.aggregator import aggregate_scores, score_metric_rubric
from app.scoring.ai_evidence import score_ai_integration

_LLM_RUBRIC = {
    "id": "ai_usage",
    "label": "Uses an LLM",
    "weight": 4,
    "weight_percent": 20,
    "max_score": 10,
    "metric": "ai_usage",
}

_PUBLIC = RepoAccessInfo("o", "r", True, True, "main", None)


def _finding(rule_id: str, status: str = "failed") -> dict:
    return {"rule_id": rule_id, "status": status, "confidence": 0.9, "evidence": []}


def _ids(rows: list[dict]) -> set[str]:
    return {row["id"] for row in rows}


def test_genuine_complete_flow_is_100_and_full_rubric():
    metrics = {
        "ai_usage": {
            "dependency_detected": True,
            "import_detected": True,
            "client_detected": True,
            "model_invocation_detected": True,
            "user_input_reaches_model": True,
            "model_output_used": True,
            "ai_findings": [],
            "ai_dependencies_found": ["openai"],
        }
    }
    breakdown = score_ai_integration(metrics)
    assert breakdown["ai_integration_score"] == 100
    assert breakdown["classification"] == "genuine_ai_integration"
    assert _ids(breakdown["positive_evidence"]) == {
        "dependency",
        "import",
        "client",
        "invocation",
        "input_flow",
        "output_used",
        "complete_flow",
    }
    assert breakdown["penalties"] == []
    row = score_metric_rubric(_LLM_RUBRIC, metrics)
    assert row["score"] == 10.0
    assert row["weighted_score"] == 4.0
    assert "deterministic integration evidence" in row["reason"]


def test_dependency_only_is_not_full_llm_marks():
    metrics = {
        "ai_usage": {
            "dependency_detected": True,
            "import_detected": False,
            "client_detected": False,
            "model_invocation_detected": False,
            "ai_dependencies_found": ["openai"],
            "ai_findings": [_finding(RULE_UNUSED_DEP)],
        }
    }
    breakdown = score_ai_integration(metrics)
    assert breakdown["ai_integration_score"] == 0
    assert breakdown["raw_total"] == -15
    assert breakdown["classification"] == "ai_dependency_only"
    assert "unused_dependency" in _ids(breakdown["penalties"])
    row = score_metric_rubric(_LLM_RUBRIC, metrics)
    assert row["score"] == 0.0
    assert row["weighted_score"] == 0.0


def test_unused_dependency_inferred_without_finding():
    metrics = {
        "ai_usage": {
            "dependency_detected": True,
            "model_invocation_detected": False,
        }
    }
    breakdown = score_ai_integration(metrics)
    assert "unused_dependency" in _ids(breakdown["penalties"])
    assert breakdown["ai_integration_score"] == 0


def test_invocation_input_discarded_is_not_complete_or_full():
    metrics = {
        "ai_usage": {
            "dependency_detected": True,
            "import_detected": True,
            "client_detected": True,
            "model_invocation_detected": True,
            "user_input_reaches_model": True,
            "model_output_used": False,
            "ai_findings": [_finding(RULE_DISCARDED)],
        }
    }
    breakdown = score_ai_integration(metrics)
    assert "complete_flow" not in _ids(breakdown["positive_evidence"])
    assert "output_used" not in _ids(breakdown["positive_evidence"])
    assert "output_discarded" in _ids(breakdown["penalties"])
    assert breakdown["ai_integration_score"] == 40
    assert breakdown["classification"] == "partial_ai_integration"
    row = score_metric_rubric(_LLM_RUBRIC, metrics)
    assert row["score"] == 4.0
    assert row["score"] < 10.0


def test_hardcoded_final_response_penalty():
    metrics = {
        "ai_usage": {
            "detected": True,
            "model_invocation_detected": False,
            "ai_findings": [_finding(RULE_HARDCODED)],
        }
    }
    breakdown = score_ai_integration(metrics)
    assert "hardcoded_response" in _ids(breakdown["penalties"])
    assert breakdown["ai_integration_score"] == 0
    assert breakdown["classification"] == "suspicious_ai_implementation"


def test_static_response_pool_penalty():
    metrics = {
        "ai_usage": {
            "detected": True,
            "model_invocation_detected": False,
            "ai_findings": [_finding(RULE_POOL)],
        }
    }
    breakdown = score_ai_integration(metrics)
    assert "static_pool" in _ids(breakdown["penalties"])
    assert next(p["points"] for p in breakdown["penalties"] if p["id"] == "static_pool") == -35


def test_mock_only_without_invocation_penalizes():
    metrics = {
        "ai_usage": {
            "dependency_detected": True,
            "model_invocation_detected": False,
            "ai_findings": [_finding(RULE_MOCK, "warning")],
        }
    }
    breakdown = score_ai_integration(metrics)
    assert "mock_only" in _ids(breakdown["penalties"])
    assert breakdown["ai_integration_score"] == 0


def test_mock_warning_does_not_penalize_real_invocation():
    metrics = {
        "ai_usage": {
            "dependency_detected": True,
            "import_detected": True,
            "client_detected": True,
            "model_invocation_detected": True,
            "user_input_reaches_model": True,
            "model_output_used": True,
            "ai_findings": [_finding(RULE_MOCK, "warning")],
        }
    }
    breakdown = score_ai_integration(metrics)
    assert "mock_only" not in _ids(breakdown["penalties"])
    assert breakdown["ai_integration_score"] == 100
    assert breakdown["classification"] == "genuine_ai_integration"


def test_score_clamped_to_0_100():
    metrics = {
        "ai_usage": {
            "dependency_detected": True,
            "model_invocation_detected": False,
            "ai_findings": [_finding(RULE_UNUSED_DEP), _finding(RULE_HARDCODED)],
        }
    }
    breakdown = score_ai_integration(metrics)
    assert 0 <= breakdown["ai_integration_score"] <= 100
    assert breakdown["raw_total"] < 0
    assert breakdown["ai_integration_score"] == 0

    inflated = score_ai_integration(
        {
            "ai_usage": {
                "dependency_detected": True,
                "import_detected": True,
                "client_detected": True,
                "model_invocation_detected": True,
                "user_input_reaches_model": True,
                "model_output_used": True,
            }
        },
        point_overrides={"points": {"invocation": 80}},
    )
    assert inflated["raw_total"] > 100
    assert inflated["ai_integration_score"] == 100


def test_point_overrides_are_configurable():
    metrics = {
        "ai_usage": {
            "dependency_detected": True,
            "model_invocation_detected": False,
        }
    }
    default = score_ai_integration(metrics)
    custom = score_ai_integration(
        metrics,
        point_overrides={"points": {"dependency": 50}, "penalties": {"unused_dependency": 0}},
    )
    assert default["ai_integration_score"] == 0
    assert custom["ai_integration_score"] == 50


def test_aggregate_exposes_ai_integration_explanations():
    metrics = {
        "fullstack": {
            "application_type": "full_stack",
            "frontend": {"detected": True, "framework": "react"},
            "backend": {"detected": True, "framework": "fastapi"},
        },
        "ai_usage": {
            "dependency_detected": True,
            "import_detected": True,
            "client_detected": True,
            "model_invocation_detected": True,
            "user_input_reaches_model": True,
            "model_output_used": True,
            "ai_findings": [],
        },
        "agent_analysis": {"status": "skipped"},
        "solution_fit": {"status": "skipped"},
    }
    scoring = aggregate_scores(metrics, access=_PUBLIC)
    block = scoring["ai_integration"]
    assert block["ai_integration_score"] == 100
    assert block["classification"] == "genuine_ai_integration"
    assert block["positive_evidence"]
    assert block["penalties"] == []
    assert "Credits:" in block["explanation"]
    llm_row = next(r for r in scoring["rubrics"] if r["id"] == "ai_usage")
    assert llm_row["score"] == 10.0


def test_request_scoring_can_override_ai_evidence_points():
    metrics = {
        "ai_usage": {
            "dependency_detected": True,
            "model_invocation_detected": False,
        }
    }
    scoring = aggregate_scores(
        metrics,
        access=_PUBLIC,
        request_scoring={"ai_evidence": {"points": {"dependency": 40}, "penalties": {"unused_dependency": 0}}},
    )
    assert scoring["ai_integration"]["ai_integration_score"] == 40
    llm_row = next(r for r in scoring["rubrics"] if r["id"] == "ai_usage")
    assert llm_row["score"] == 4.0


def test_legacy_agentic_payload_without_phase8_keys_still_full_score():
    metrics = {
        "ai_usage": {
            "ai_integration_type": "agentic",
            "ai_dependencies_found": ["openai"],
            "llm_providers": {
                "uses_llm": True,
                "provider_names": ["OpenAI"],
                "model_hints": ["gpt-4o"],
                "reasoning": "OpenAI in code",
            },
        }
    }
    row = score_metric_rubric(_LLM_RUBRIC, metrics)
    assert row["score"] == 10.0
    assert row["weighted_score"] == 4.0
    assert "deterministic" not in row["reason"]


def test_live_genuine_wrapper_scores_full_llm_rubric():
    source = '''
from openai import OpenAI

def generate_ai(prompt):
    client = OpenAI()
    return client.responses.create(prompt)

@app.post("/chat")
def chat(body):
    result = generate_ai(body.message)
    return {"answer": result}
'''
    snapshot = RepoSnapshot(
        ref=RepoRef("o", "r"),
        tree=[{"path": "backend/chat.py", "type": "blob"}],
        file_contents={"backend/chat.py": source},
    )
    facts = build_code_facts(snapshot)
    result = asyncio.run(
        AiUsageMetric().run(MetricContext(snapshot=snapshot, extras={"code_facts": facts}))
    )
    scoring = aggregate_scores({"ai_usage": result.data}, access=_PUBLIC)
    assert scoring["ai_integration"]["ai_integration_score"] == 100
    assert scoring["ai_integration"]["classification"] == "genuine_ai_integration"
    llm_row = next(r for r in scoring["rubrics"] if r["id"] == "ai_usage")
    assert llm_row["score"] == 10.0


def test_live_discarded_output_does_not_earn_full_llm_rubric():
    source = '''
from openai import OpenAI

def generate_ai(prompt):
    client = OpenAI()
    return client.responses.create(prompt)

@app.post("/chat")
def chat(body):
    result = generate_ai(body.message)
    return "Thank you!"
'''
    snapshot = RepoSnapshot(
        ref=RepoRef("o", "r"),
        tree=[{"path": "backend/chat.py", "type": "blob"}],
        file_contents={"backend/chat.py": source},
    )
    facts = build_code_facts(snapshot)
    result = asyncio.run(
        AiUsageMetric().run(MetricContext(snapshot=snapshot, extras={"code_facts": facts}))
    )
    scoring = aggregate_scores({"ai_usage": result.data}, access=_PUBLIC)
    assert scoring["ai_integration"]["ai_integration_score"] < 100
    assert "output_discarded" in _ids(scoring["ai_integration"]["penalties"])
    assert "complete_flow" not in _ids(scoring["ai_integration"]["positive_evidence"])
    llm_row = next(r for r in scoring["rubrics"] if r["id"] == "ai_usage")
    assert llm_row["score"] < 10.0
