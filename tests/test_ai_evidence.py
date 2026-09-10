"""Phase 8: AI evidence levels from Code Facts. No Gemini."""

from __future__ import annotations

import asyncio

from app.analysis.facts import build_code_facts
from app.github.client import RepoRef, RepoSnapshot
from app.metrics.ai_usage import AiUsageMetric
from app.metrics.base import MetricContext


def _run(
    files: dict[str, str],
    *,
    manifests: dict[str, str] | None = None,
    context: dict | None = None,
):
    tree = [{"path": path, "type": "blob"} for path in {**files, **(manifests or {})}]
    snapshot = RepoSnapshot(
        ref=RepoRef("o", "r"),
        tree=tree,
        file_contents=files,
        package_manifests=manifests or {},
    )
    extras = {
        "code_facts": build_code_facts(snapshot),
        "skip_file_fetch": True,
    }
    if context:
        extras["submission_context"] = context
    return asyncio.run(AiUsageMetric().run(MetricContext(snapshot=snapshot, extras=extras)))


def test_level0_no_ai_evidence():
    result = _run(
        {"backend/app.py": "def main():\n    print('hello')\n"},
        manifests={"requirements.txt": "flask==3.0.0\n"},
    )
    data = result.data
    assert data["detected"] is False
    assert data["integration_level"] == 0
    assert data["providers"] == []
    assert data["dependency_detected"] is False
    assert data["import_detected"] is False
    assert data["client_detected"] is False
    assert data["model_invocation_detected"] is False
    assert data["ai_integration_type"] == "none"


def test_level1_readme_only():
    result = _run(
        {
            "README.md": "# Demo\nPowered by Google Gemini for answers.\n",
            "backend/app.py": "def main():\n    return 'ok'\n",
        },
        manifests={"requirements.txt": "fastapi\n"},
    )
    data = result.data
    assert data["detected"] is True
    assert data["integration_level"] == 1
    assert data["providers"] == ["gemini"]
    assert data["dependency_detected"] is False
    assert data["import_detected"] is False
    assert data["client_detected"] is False
    assert data["model_invocation_detected"] is False
    assert data["ai_integration_type"] == "none"
    assert data["llm_providers"]["uses_llm"] is False


def test_level2_package_installed_only():
    result = _run(
        {"backend/app.py": "def main():\n    return 'ok'\n"},
        manifests={"requirements.txt": "openai==1.40.0\n"},
    )
    data = result.data
    assert data["detected"] is True
    assert data["integration_level"] == 2
    assert data["providers"] == ["openai"]
    assert data["dependency_detected"] is True
    assert data["import_detected"] is False
    assert data["client_detected"] is False
    assert data["model_invocation_detected"] is False
    assert "openai" in data["ai_dependencies_found"]


def test_level3_imported_only():
    result = _run(
        {"backend/llm.py": "from openai import OpenAI\n"},
        manifests={"requirements.txt": "openai\n"},
    )
    data = result.data
    assert data["integration_level"] == 3
    assert data["providers"] == ["openai"]
    assert data["import_detected"] is True
    assert data["client_detected"] is False
    assert data["model_invocation_detected"] is False


def test_level3_client_created_never_called():
    result = _run(
        {
            "backend/llm.py": (
                "from openai import OpenAI\n"
                "def build():\n"
                "    client = OpenAI()\n"
                "    return client\n"
            )
        }
    )
    data = result.data
    assert data["integration_level"] == 3
    assert data["import_detected"] is True
    assert data["client_detected"] is True
    assert data["model_invocation_detected"] is False


def test_level4_openai_invocation():
    result = _run(
        {
            "backend/llm.py": (
                "from openai import OpenAI\n"
                "def generate_answer(prompt):\n"
                "    client = OpenAI()\n"
                "    return client.responses.create(prompt)\n"
            )
        }
    )
    data = result.data
    assert data["detected"] is True
    assert data["providers"] == ["openai"]
    assert data["integration_level"] == 4
    assert data["dependency_detected"] is False
    assert data["import_detected"] is True
    assert data["client_detected"] is True
    assert data["model_invocation_detected"] is True
    assert data["llm_providers"]["uses_llm"] is True


def test_level4_gemini_generate_content():
    result = _run(
        {
            "backend/gemini.py": (
                "from vertexai.generative_models import GenerativeModel\n"
                "def ask(prompt):\n"
                "    model = GenerativeModel('gemini-pro')\n"
                "    return model.generate_content(prompt)\n"
            )
        }
    )
    data = result.data
    assert data["providers"] == ["gemini"]
    assert data["integration_level"] == 4
    assert data["client_detected"] is True
    assert data["model_invocation_detected"] is True


def test_level4_anthropic_messages_create():
    result = _run(
        {
            "backend/claude.py": (
                "from anthropic import Anthropic\n"
                "def ask(prompt):\n"
                "    client = Anthropic()\n"
                "    return client.messages.create(prompt)\n"
            )
        }
    )
    data = result.data
    assert "anthropic" in data["providers"]
    assert data["integration_level"] == 4
    assert data["model_invocation_detected"] is True


def test_multiple_ai_providers():
    result = _run(
        {
            "backend/router.py": (
                "from openai import OpenAI\n"
                "from anthropic import Anthropic\n"
                "def ask(prompt):\n"
                "    a = OpenAI().responses.create(prompt)\n"
                "    b = Anthropic().messages.create(prompt)\n"
                "    return a or b\n"
            )
        }
    )
    data = result.data
    assert data["integration_level"] == 4
    assert data["providers"] == ["openai", "anthropic"]
    assert data["model_invocation_detected"] is True


def test_level4_groq_chat_completions():
    result = _run(
        {
            "backend/groq_client.py": (
                "from groq import Groq\n"
                "def ask(prompt):\n"
                "    client = Groq()\n"
                "    return client.chat.completions.create(prompt)\n"
            )
        }
    )
    data = result.data
    assert data["providers"] == ["groq"]
    assert data["integration_level"] == 4
    assert data["model_invocation_detected"] is True


def test_level4_mistral_chat_complete():
    result = _run(
        {
            "backend/mistral_client.py": (
                "from mistralai import Mistral\n"
                "def ask(prompt):\n"
                "    client = Mistral()\n"
                "    return client.chat.complete(prompt)\n"
            )
        }
    )
    data = result.data
    assert data["providers"] == ["mistral"]
    assert data["integration_level"] == 4


def test_level3_langchain_import_is_not_invocation():
    result = _run(
        {"backend/chain.py": "from langchain_openai import ChatOpenAI\n"},
        manifests={"requirements.txt": "langchain-openai\n"},
    )
    data = result.data
    assert "langchain" in data["providers"]
    assert "openai" in data["providers"]
    assert data["integration_level"] == 3
    assert data["model_invocation_detected"] is False


def test_readme_keyword_is_not_invocation():
    result = _run(
        {
            "README.md": "We will use OpenAI client.responses.create and Gemini generate_content.\n",
            "backend/app.py": "def main():\n    return 1\n",
        }
    )
    data = result.data
    assert data["integration_level"] == 1
    assert data["model_invocation_detected"] is False


def test_existing_no_deps_case_still_none():
    result = _run({}, manifests={"requirements.txt": "flask==3.0.0\n"})
    assert result.data["ai_integration_type"] == "none"
    assert result.data["ai_dependencies_found"] == []
    assert result.data["integration_level"] == 0
