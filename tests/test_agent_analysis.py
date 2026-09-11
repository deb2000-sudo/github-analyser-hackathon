"""Phase 14: deterministic agent evidence + mocked Gemini classification."""

from __future__ import annotations

import asyncio

from app.analysis.agent_evidence import collect_agent_evidence, normalize_agent_judgment
from app.analysis.facts import build_code_facts
from app.github.client import RepoRef, RepoSnapshot
from app.llm.reasoner import LLMReasoner, ReasoningRequest
from app.metrics.agent_analysis import AgentAnalysisMetric
from app.metrics.base import MetricContext
from app.pipeline.prompt import build_system_prompt

_LANGGRAPH = '''
from langgraph.graph import StateGraph, END

class GraphState:
    pass

def planner(state):
    return state

def researcher(state):
    return state

def should_continue(state):
    return "researcher"

graph = StateGraph(GraphState)
graph.add_node("planner", planner)
graph.add_node("researcher", researcher)
graph.add_edge("planner", "researcher")
graph.add_conditional_edges("researcher", should_continue)
'''

_WRAPPER = '''
from openai import OpenAI

def generate_ai(prompt):
    client = OpenAI()
    return client.responses.create(prompt)
'''

_TOOLS = '''
from langchain_core.tools import tool

@tool
def search(query: str) -> str:
    return query

def run(llm):
    return llm.bind_tools([search])
'''

_DEPS_ONLY_README = """# Agents
This project uses LangGraph and LangChain agents.
"""


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


def _snapshot(files: dict[str, str], manifests: dict[str, str] | None = None) -> RepoSnapshot:
    tree = [{"path": path, "type": "blob"} for path in files]
    return RepoSnapshot(
        ref=RepoRef("o", "r"),
        tree=tree,
        file_contents=files,
        package_manifests=manifests or {},
    )


def _facts(files: dict[str, str], manifests: dict[str, str] | None = None):
    snap = _snapshot(files, manifests)
    return snap, build_code_facts(snap)


def test_collects_langgraph_nodes_edges_and_loops():
    _, facts = _facts({"backend/graph.py": _LANGGRAPH})
    evidence = collect_agent_evidence(
        facts.source_facts,
        call_graph=facts.call_graph,
        file_contents={"backend/graph.py": _LANGGRAPH},
    )
    assert evidence["needs_semantic"] is True
    assert evidence["graph_nodes"]
    assert evidence["graph_edges"]
    assert any(item["kind"] == "NODE" for item in evidence["items"])
    assert any(item["kind"] == "EDGE" for item in evidence["items"])
    assert any(item["kind"] == "LOOP" for item in evidence["items"])
    assert any("langgraph" in str(evidence["framework_dependencies"]["imported"]).lower() for _ in [0])


def test_collects_tool_registrations():
    _, facts = _facts({"backend/tools.py": _TOOLS})
    evidence = collect_agent_evidence(facts.source_facts)
    assert evidence["tool_registrations"]
    assert evidence["needs_semantic"] is True
    assert any(item["kind"] == "TOOL_REG" for item in evidence["items"])


def test_wrapper_collects_invocations_not_graph():
    _, facts = _facts({"backend/chat.py": _WRAPPER})
    evidence = collect_agent_evidence(facts.source_facts)
    assert evidence["model_invocations"]
    assert evidence["graph_nodes"] == []
    assert evidence["graph_edges"] == []
    assert evidence["needs_semantic"] is True


def test_manifest_and_readme_are_not_enough():
    files = {
        "README.md": _DEPS_ONLY_README,
        "backend/app.py": "def main():\n    return 'ok'\n",
    }
    snap, facts = _facts(files, {"requirements.txt": "langgraph==0.2.0\nlangchain==0.2.0\n"})
    evidence = collect_agent_evidence(
        facts.source_facts,
        manifests=snap.package_manifests,
        file_contents=files,
        ai_usage={"agent_frameworks_found": ["langgraph"]},
    )
    assert evidence["naming_or_manifest_only"] is True
    assert evidence["needs_semantic"] is False
    assert "langgraph" in evidence["framework_dependencies"]["declared"] or evidence["framework_dependencies"]["readme_mentions"]


def test_normalize_rejects_genuine_without_evidence_ids():
    evidence = {
        "items": [{"id": "AGT.FRAMEWORK:requirements.txt:1:declared_langgraph", "kind": "FRAMEWORK"}],
        "model_invocations": [],
        "tool_registrations": [],
        "tool_calls": [],
        "naming_or_manifest_only": True,
    }
    out = normalize_agent_judgment(
        {"classification": "GENUINE_AGENT_ORCHESTRATION", "evidence_ids": [], "reasoning": "saw langgraph"},
        evidence,
    )
    assert out["classification"] == "NO_AGENT"
    assert out["has_real_orchestration"] is False


def test_metric_sends_agent_evidence_and_uses_mocked_classification():
    snap, facts = _facts({"backend/graph.py": _LANGGRAPH})
    evidence = collect_agent_evidence(facts.source_facts, call_graph=facts.call_graph)
    node_id = next(item["id"] for item in evidence["items"] if item["kind"] == "NODE")
    edge_id = next(item["id"] for item in evidence["items"] if item["kind"] == "EDGE")
    reasoner = FakeReasoner(
        {
            "agent_analysis": {
                "classification": "GENUINE_AGENT_ORCHESTRATION",
                "agent_count": 2,
                "agents": [
                    {"role_guess": "planner", "file": "backend/graph.py", "evidence": "add_node", "evidence_ids": [node_id]}
                ],
                "has_real_orchestration": True,
                "evidence_ids": [node_id, edge_id],
                "confidence": "high",
                "reasoning": "StateGraph with nodes, edges, and conditional routing.",
            }
        }
    )
    result = asyncio.run(
        AgentAnalysisMetric().run(
            MetricContext(
                snapshot=snap,
                extras={"llm_client": reasoner, "code_facts": facts, "agent_frameworks_found": ["langgraph"]},
                prior_results={"ai_usage": {"model_invocation_detected": False, "confidence": "high"}},
            )
        )
    )
    assert result.status == "ok"
    assert result.data["classification"] == "GENUINE_AGENT_ORCHESTRATION"
    assert result.data["has_real_orchestration"] is True
    assert node_id in result.data["evidence_ids"]
    assert reasoner.calls
    pack = reasoner.calls[0].evidence
    assert pack["agent_evidence"]["graph_nodes"]
    assert pack["agent_evidence"]["graph_edges"]
    prompt = build_system_prompt(["agent_analysis"], questions=reasoner.calls[0].questions)
    assert "Do not classify as an agent merely because" in prompt
    assert "GENUINE_AGENT_ORCHESTRATION" in prompt


def test_metric_wrapper_fixture_is_not_an_agent():
    snap, facts = _facts({"backend/chat.py": _WRAPPER})
    evidence = collect_agent_evidence(facts.source_facts)
    invoke_id = next(item["id"] for item in evidence["items"] if item["kind"] == "INVOKE")
    reasoner = FakeReasoner(
        {
            "agent_analysis": {
                "classification": "LLM_WRAPPER",
                "agent_count": 0,
                "agents": [],
                "evidence_ids": [invoke_id],
                "confidence": "high",
                "reasoning": "Single model call, no graph or tools.",
            }
        }
    )
    result = asyncio.run(
        AgentAnalysisMetric().run(
            MetricContext(
                snapshot=snap,
                extras={"llm_client": reasoner, "code_facts": facts, "agent_frameworks_found": []},
                prior_results={"ai_usage": {"model_invocation_detected": True, "confidence": "high"}},
            )
        )
    )
    assert result.data["classification"] == "LLM_WRAPPER"
    assert result.data["has_real_orchestration"] is False


def test_metric_skips_gemini_for_readme_only_langgraph():
    files = {"README.md": _DEPS_ONLY_README, "app.py": "print('hi')\n"}
    snap, facts = _facts(files, {"requirements.txt": "langgraph\n"})
    reasoner = FakeReasoner({"agent_analysis": {"classification": "GENUINE_AGENT_ORCHESTRATION"}})
    result = asyncio.run(
        AgentAnalysisMetric().run(
            MetricContext(
                snapshot=snap,
                extras={"llm_client": reasoner, "code_facts": facts, "agent_frameworks_found": ["langgraph"]},
                prior_results={"ai_usage": {"model_invocation_detected": False, "confidence": "high"}},
            )
        )
    )
    assert reasoner.calls == []
    assert result.data["classification"] == "NO_AGENT"
    assert result.data["has_real_orchestration"] is False
