"""LLM provider detection tests."""

from __future__ import annotations

from app.scoring.llm_providers import detect_llm_providers


def test_detect_gemini_in_code():
    result = detect_llm_providers(
        [],
        {"app/main.py": 'model = "gemini-2.5-flash"\nclient = GenerativeModel(model)'},
    )
    assert result["uses_llm"] is True
    assert "Google Gemini" in result["provider_names"]
    assert "gemini" in result["model_hints"][0].lower()


def test_detect_openai_in_code():
    result = detect_llm_providers(
        ["openai"],
        {"app/llm.py": 'from openai import OpenAI\nclient = OpenAI()\nmodel = "gpt-4o"'},
    )
    assert result["uses_llm"] is True
    assert "OpenAI" in result["provider_names"]
    assert "gpt-4o" in result["model_hints"]


def test_detect_groq_from_dependency():
    result = detect_llm_providers(["groq"], {})
    assert result["uses_llm"] is True
    assert "Groq" in result["provider_names"]


def test_detect_multiple_providers():
    result = detect_llm_providers(
        ["openai", "anthropic"],
        {"app/router.py": 'model = "claude-3-5-sonnet"\n# also supports gpt-4o'},
    )
    assert result["uses_llm"] is True
    assert "OpenAI" in result["provider_names"]
    assert "Anthropic" in result["provider_names"]


def test_no_llm_detected():
    result = detect_llm_providers([], {"app/main.py": "print('hello')"})
    assert result["uses_llm"] is False
