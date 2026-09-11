"""Phase 15: solution_fit compares claimed context to implementation evidence."""

from __future__ import annotations

import asyncio

from app.analysis.facts import build_code_facts
from app.analysis.solution_evidence import build_solution_evidence_pack
from app.github.client import RepoRef, RepoSnapshot
from app.llm.reasoner import LLMReasoner, ReasoningRequest
from app.metrics.base import MetricContext
from app.metrics.solution_fit import SolutionFitMetric
from app.pipeline.prompt import UNTRUSTED_RULES, build_system_prompt, build_user_prompt

_APP = '''
from fastapi import FastAPI
from openai import OpenAI

app = FastAPI()

@app.post("/api/chat")
def chat(body):
    client = OpenAI()
    result = client.responses.create(body.message)
    return {"answer": result}
'''


class FakeReasoner(LLMReasoner):
    def __init__(self, payload: dict):
        self.payload = payload
        self.calls: list[ReasoningRequest] = []

    @property
    def enabled(self) -> bool:
        return True

    async def reason(self, request: ReasoningRequest) -> dict:
        self.calls.append(request)
        return self.payload


def _snapshot() -> tuple[RepoSnapshot, object]:
    files = {
        "backend/app.py": _APP,
        "README.md": "# Chat\nInstall with pip.\nRun uvicorn backend.app:app\n",
    }
    snap = RepoSnapshot(
        ref=RepoRef("o", "r"),
        tree=[{"path": p, "type": "blob"} for p in files],
        file_contents=files,
        package_manifests={"backend/requirements.txt": "fastapi\nopenai\n"},
    )
    return snap, build_code_facts(snap)


def test_solution_pack_is_not_the_repository():
    snap, facts = _snapshot()
    huge = "print('pad')\n" * 2000
    snap.file_contents["backend/app.py"] = _APP + huge
    pack = build_solution_evidence_pack(
        code_facts=facts,
        snapshot=snap,
        static_metrics={
            "fullstack": {
                "application_type": "backend",
                "backend": {"detected": True, "framework": "fastapi"},
                "frontend": {"detected": False},
            },
            "ai_usage": {
                "model_invocation_detected": True,
                "user_input_reaches_model": True,
                "model_output_used": True,
                "providers": ["openai"],
                "evidence_files": ["backend/app.py"],
            },
            "agent_analysis": {
                "classification": "LLM_WRAPPER",
                "has_real_orchestration": False,
                "evidence_ids": ["AGT.INVOKE:backend/app.py:8:responses.create"],
            },
        },
        submission_context={"provided_context": "Build a study planner that chats with an LLM."},
    )
    assert pack["claimed_project"].startswith("Build a study planner")
    assert pack["detected_architecture"]["application_type"] == "backend"
    assert pack["frameworks"]["backend"] == "fastapi"
    assert pack["major_files"]
    assert any(r.get("path") == "/api/chat" for r in pack["api_routes"])
    assert pack["ai_integration"]["model_invocation_detected"] is True
    assert pack["agent_result"]["classification"] == "LLM_WRAPPER"
    assert pack["readme_summary"]["preview"]
    assert pack["source_snippets"]
    assert all(len(s["text"]) < 2000 for s in pack["source_snippets"])
    assert pack["call_paths"] is not None
    assert pack["data_flow_paths"] is not None
    assert huge not in str(pack)
    for snippet in pack["source_snippets"]:
        assert huge not in snippet["text"]
    assert any(item["id"].startswith("FIT.ROUTE") for item in pack["evidence"])
    assert any(item["id"].startswith("FIT.AI") for item in pack["evidence"])
    assert any(item["id"].startswith("FIT.AGENT") for item in pack["evidence"])


def test_metric_uses_mocked_gemini_structured_features():
    snap, facts = _snapshot()
    pack = build_solution_evidence_pack(
        code_facts=facts,
        snapshot=snap,
        static_metrics={
            "fullstack": {"application_type": "backend", "backend": {"detected": True, "framework": "fastapi"}},
            "ai_usage": {"model_invocation_detected": True},
            "agent_analysis": {"classification": "LLM_WRAPPER"},
        },
        submission_context={"provided_context": "A FastAPI chat API that calls OpenAI."},
    )
    ids = [item["id"] for item in pack["evidence"]]
    route_id = next(i for i in ids if i.startswith("FIT.ROUTE"))
    ai_id = next((i for i in ids if i.startswith("FIT.AI")), ids[0])
    reasoner = FakeReasoner(
        {
            "solution_fit": {
                "context_relevant": True,
                "relevance_score": 8,
                "alignment_score": 7,
                "implements_claimed_solution": True,
                "implementation_matches_claim": True,
                "verified_features": [
                    {"feature": "FastAPI chat route", "evidence_ids": [route_id]},
                    {"feature": "OpenAI invocation", "evidence_ids": [ai_id]},
                ],
                "unsupported_features": [{"feature": "multi-agent planner", "evidence_ids": []}],
                "partial_features": [
                    {
                        "feature": "streaming responses",
                        "evidence_ids": [route_id],
                        "note": "route exists, streaming not proven",
                    }
                ],
                "evidence_ids": [route_id, ai_id],
                "confidence": "high",
                "reasoning": "Implementation matches a chat API, not a planner.",
            }
        }
    )
    result = asyncio.run(
        SolutionFitMetric().run(
            MetricContext(
                snapshot=snap,
                extras={
                    "llm_client": reasoner,
                    "code_facts": facts,
                    "submission_context": {"provided_context": "A FastAPI chat API that calls OpenAI."},
                },
                prior_results={
                    "fullstack": {"application_type": "backend", "backend": {"detected": True, "framework": "fastapi"}},
                    "ai_usage": {"model_invocation_detected": True},
                    "agent_analysis": {"classification": "LLM_WRAPPER", "has_real_orchestration": False},
                },
            )
        )
    )
    assert result.status == "ok"
    assert result.data["implementation_matches_claim"] is True
    assert result.data["verified_features"]
    assert result.data["verified_features"][0]["evidence_ids"]
    assert result.data["partial_features"]
    assert result.data["partial_features"][0]["feature"] == "streaming responses"
    assert result.data["unsupported_features"]
    assert result.data["confidence"] == "high"
    assert result.data["evidence_ids"]
    assert reasoner.calls
    sent = reasoner.calls[0].evidence
    assert sent["claimed_project"]
    assert sent["api_routes"]
    assert sent["ai_integration"]
    assert sent["agent_result"]["classification"] == "LLM_WRAPPER"
    for key in (
        "detected_architecture",
        "frameworks",
        "major_files",
        "api_routes",
        "ai_integration",
        "agent_result",
        "readme_summary",
        "source_evidence",
        "source_snippets",
        "call_paths",
        "data_flow_paths",
        "evidence",
    ):
        assert key in sent
    assert sent["source_snippets"]
    assert all(len(s.get("text") or "") <= 1000 for s in sent["source_snippets"])
    user = build_user_prompt(
        metrics=["solution_fit"],
        evidence_pack=sent,
        submission_context={"provided_context": "A FastAPI chat API that calls OpenAI."},
        questions=reasoner.calls[0].questions,
    )
    assert "UNTRUSTED REPOSITORY EVIDENCE" in user
    system = build_system_prompt(["solution_fit"], questions=reasoner.calls[0].questions)
    for line in UNTRUSTED_RULES.splitlines():
        assert line in system
    assert "verified / unsupported / partial" in system or "verified_features" in system


def test_unrelated_claim_keeps_zero_alignment_from_mock():
    snap, facts = _snapshot()
    reasoner = FakeReasoner(
        {
            "solution_fit": {
                "context_relevant": False,
                "relevance_score": 1,
                "alignment_score": 9,
                "implements_claimed_solution": True,
                "verified_features": [],
                "unsupported_features": [{"feature": "study planner", "evidence_ids": ["FIT.ARCH:backend"]}],
                "partial_features": [],
                "evidence_ids": ["FIT.ARCH:backend"],
                "reasoning": "Chat API is not a study planner.",
            }
        }
    )
    result = asyncio.run(
        SolutionFitMetric().run(
            MetricContext(
                snapshot=snap,
                extras={
                    "llm_client": reasoner,
                    "code_facts": facts,
                    "submission_context": {"provided_context": "Build a multi-agent study planner."},
                },
                prior_results={
                    "fullstack": {"application_type": "backend", "backend": {"framework": "fastapi"}},
                    "agent_analysis": {"classification": "LLM_WRAPPER"},
                },
            )
        )
    )
    assert result.data["context_relevant"] is False
    assert result.data["alignment_score"] == 0.0
    assert result.data["implementation_matches_claim"] is False
    assert result.data["unsupported_features"]
