"""Phase 11: EvidenceAggregator combines signals; detectors do not set final truth."""

from __future__ import annotations

from types import SimpleNamespace

from app.analysis.ai_fake import RULE_DISCARDED, RULE_HARDCODED, RULE_UNUSED_DEP
from app.analysis.evidence_aggregator import (
    AI_CODE_PRESENT,
    AI_DEPENDENCY_ONLY,
    AI_INVOCATION_PRESENT,
    AI_MENTION_ONLY,
    GENUINE_AI_INTEGRATION,
    NO_AI,
    PARTIAL_AI_INTEGRATION,
    SUSPICIOUS_AI_IMPLEMENTATION,
    EvidenceAggregator,
)


def _ai_conclusion(verdict: dict) -> dict:
    return next(c for c in verdict["conclusions"] if c["id"] == "AI_CLASSIFICATION")


def test_no_ai():
    verdict = EvidenceAggregator().aggregate(metrics={})
    assert verdict["ai_classification"] == NO_AI
    assert verdict["ai_level"] == 0
    assert "score" not in verdict
    assert isinstance(verdict["confidence"], float)
    conclusion = _ai_conclusion(verdict)
    assert conclusion["evidence_ids"]
    assert conclusion["confidence"] == verdict["confidence"]


def test_mention_only():
    verdict = EvidenceAggregator().aggregate(
        metrics={
            "ai_usage": {
                "detected": True,
                "providers": ["gemini"],
                "dependency_detected": False,
                "import_detected": False,
                "client_detected": False,
                "model_invocation_detected": False,
                "integration_level": 4,
                "ai_integration_type": "wrapper",
            }
        }
    )
    assert verdict["ai_classification"] == AI_MENTION_ONLY
    assert verdict["ai_level"] == 1
    assert "AI.MENTION" in _ai_conclusion(verdict)["evidence_ids"]


def test_dependency_only_unused_package():
    verdict = EvidenceAggregator().aggregate(
        metrics={
            "ai_usage": {
                "detected": True,
                "dependency_detected": True,
                "ai_dependencies_found": ["openai"],
                "import_detected": False,
                "client_detected": False,
                "model_invocation_detected": False,
                "ai_findings": [
                    {
                        "rule_id": RULE_UNUSED_DEP,
                        "status": "failed",
                        "confidence": 0.93,
                        "evidence": [{"file": "requirements.txt", "line": 1}],
                    }
                ],
            }
        }
    )
    assert verdict["ai_classification"] == AI_DEPENDENCY_ONLY
    assert verdict["ai_level"] == 2
    ids = _ai_conclusion(verdict)["evidence_ids"]
    assert "AI.DEPENDENCY" in ids
    assert RULE_UNUSED_DEP in ids


def test_sdk_import_without_invocation():
    verdict = EvidenceAggregator().aggregate(
        metrics={
            "ai_usage": {
                "detected": True,
                "dependency_detected": True,
                "import_detected": True,
                "client_detected": True,
                "model_invocation_detected": False,
            }
        }
    )
    assert verdict["ai_classification"] == AI_CODE_PRESENT
    assert verdict["ai_level"] == 3


def test_invocation_without_flow():
    verdict = EvidenceAggregator().aggregate(
        metrics={
            "ai_usage": {
                "detected": True,
                "import_detected": True,
                "model_invocation_detected": True,
                "user_input_reaches_model": False,
                "model_output_used": False,
            }
        }
    )
    assert verdict["ai_classification"] == AI_INVOCATION_PRESENT
    assert verdict["ai_level"] == 4


def test_genuine_requires_input_and_output():
    verdict = EvidenceAggregator().aggregate(
        metrics={
            "ai_usage": {
                "detected": True,
                "import_detected": True,
                "model_invocation_detected": True,
                "user_input_reaches_model": True,
                "model_output_used": True,
            }
        }
    )
    assert verdict["ai_classification"] == GENUINE_AI_INTEGRATION
    assert verdict["ai_level"] == 6
    ids = _ai_conclusion(verdict)["evidence_ids"]
    assert ids == ["AI.INVOCATION", "AI.INPUT_REACHES_MODEL", "AI.OUTPUT_USED"]


def test_contradiction_discarded_output_is_not_genuine():
    verdict = EvidenceAggregator().aggregate(
        metrics={
            "ai_usage": {
                "detected": True,
                "import_detected": True,
                "model_invocation_detected": True,
                "user_input_reaches_model": True,
                "model_output_used": False,
                "ai_integration_type": "wrapper",
                "integration_level": 4,
                "ai_findings": [
                    {
                        "rule_id": RULE_DISCARDED,
                        "status": "failed",
                        "confidence": 0.94,
                        "evidence": [{"file": "backend/chat.py", "line": 12}],
                    }
                ],
            }
        }
    )
    assert verdict["ai_classification"] == PARTIAL_AI_INTEGRATION
    assert verdict["ai_classification"] != GENUINE_AI_INTEGRATION
    assert verdict["ai_level"] == 5
    ids = _ai_conclusion(verdict)["evidence_ids"]
    assert "AI.INVOCATION" in ids
    assert "AI.INPUT_REACHES_MODEL" in ids
    assert RULE_DISCARDED in ids


def test_literal_prompt_with_used_output_is_partial():
    verdict = EvidenceAggregator().aggregate(
        metrics={
            "ai_usage": {
                "model_invocation_detected": True,
                "user_input_reaches_model": False,
                "model_output_used": True,
            }
        }
    )
    assert verdict["ai_classification"] == PARTIAL_AI_INTEGRATION
    assert verdict["ai_level"] == 4


def test_hardcoded_response_is_suspicious():
    verdict = EvidenceAggregator().aggregate(
        metrics={
            "ai_usage": {
                "detected": True,
                "model_invocation_detected": False,
                "ai_findings": [
                    {
                        "rule_id": RULE_HARDCODED,
                        "status": "failed",
                        "confidence": 0.97,
                        "evidence": [{"file": "backend/chat.py", "line": 24}],
                    }
                ],
            }
        }
    )
    assert verdict["ai_classification"] == SUSPICIOUS_AI_IMPLEMENTATION
    assert RULE_HARDCODED in _ai_conclusion(verdict)["evidence_ids"]


def test_mock_warning_does_not_override_genuine():
    verdict = EvidenceAggregator().aggregate(
        metrics={
            "ai_usage": {
                "model_invocation_detected": True,
                "user_input_reaches_model": True,
                "model_output_used": True,
                "ai_findings": [
                    {
                        "rule_id": "AI.MOCK_AI_IMPLEMENTATION",
                        "status": "warning",
                        "confidence": 0.55,
                        "evidence": [{"file": "backend/chat.py", "line": 3}],
                    }
                ],
            }
        }
    )
    assert verdict["ai_classification"] == GENUINE_AI_INTEGRATION


def test_application_type_from_atomic_flags_not_detector_label():
    verdict = EvidenceAggregator().aggregate(
        metrics={
            "fullstack": {
                "application_type": "unknown",
                "frontend": {"detected": True, "framework": "react"},
                "backend": {"detected": True, "framework": "fastapi"},
            },
            "frontend_backend": {"connected": True},
        }
    )
    app = next(c for c in verdict["conclusions"] if c["id"] == "APPLICATION_TYPE")
    conn = next(c for c in verdict["conclusions"] if c["id"] == "FRONTEND_BACKEND")
    assert app["classification"] == "full_stack"
    assert "FS.FRONTEND_DETECTED" in app["evidence_ids"]
    assert "FS.BACKEND_DETECTED" in app["evidence_ids"]
    assert conn["classification"] == "connected"
    assert conn["evidence_ids"]


def test_structure_layout_does_not_override_missing_backend():
    facts = SimpleNamespace(
        structure={"layout": "fullstack", "is_monorepo": True, "has_frontend_paths": True},
        inventory={"file_count": 12, "layout": "fullstack"},
    )
    verdict = EvidenceAggregator().aggregate(
        metrics={
            "fullstack": {
                "application_type": "full_stack",
                "frontend": {"detected": True, "framework": "react"},
                "backend": {"detected": False, "framework": None},
            }
        },
        code_facts=facts,
    )
    app = next(c for c in verdict["conclusions"] if c["id"] == "APPLICATION_TYPE")
    assert app["classification"] == "frontend"
    assert app["confidence"] < 0.9
    assert "STR.LAYOUT" in app["evidence_ids"]


def test_every_conclusion_has_evidence_ids():
    verdict = EvidenceAggregator().aggregate(metrics={})
    for conclusion in verdict["conclusions"]:
        assert conclusion["evidence_ids"]
        assert "confidence" in conclusion
        assert "score" not in conclusion


def test_live_metrics_discarded_output_is_not_genuine():
    import asyncio

    from app.analysis.facts import build_code_facts
    from app.github.client import RepoRef, RepoSnapshot
    from app.metrics.ai_usage import AiUsageMetric
    from app.metrics.base import MetricContext

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
    verdict = EvidenceAggregator().aggregate(
        metrics={"ai_usage": result.data},
        code_facts=facts,
    )
    assert result.data["model_invocation_detected"] is True
    assert result.data["user_input_reaches_model"] is True
    assert result.data["model_output_used"] is False
    assert verdict["ai_classification"] == PARTIAL_AI_INTEGRATION
    assert verdict["ai_classification"] != GENUINE_AI_INTEGRATION
    assert verdict["ai_level"] == 5

