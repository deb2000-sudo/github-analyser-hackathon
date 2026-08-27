"""Rubric scoring aggregation tests."""

from __future__ import annotations

from app.github.validation import RepoAccessInfo
from app.scoring.aggregator import aggregate_scores, build_gated_result, score_metric_rubric
from app.scoring.rubrics import DEFAULT_RUBRICS, resolve_rubrics


def test_gated_private_repo_scores_zero():
    access = RepoAccessInfo(
        owner="o",
        name="r",
        is_public=False,
        exists=True,
        default_branch="main",
        reason="repository_is_private",
    )
    scoring = aggregate_scores({}, access=access)
    assert scoring["total_score"] == 0.0
    assert len(scoring["rubrics"]) == len(DEFAULT_RUBRICS)
    assert all(r["score"] == 0.0 for r in scoring["rubrics"])
    assert all(r["reason"] == "repository_is_private" for r in scoring["rubrics"])


def test_build_gated_result_structure():
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
    assert result["access"]["is_public"] is False
    assert result["scoring"]["total_score"] == 0.0
    assert result["metrics"] == {}


def test_fullstack_rubric_full_score():
    rubric = {"id": "fullstack", "label": "Full-stack", "weight": 10, "weight_percent": 50, "max_score": 10, "metric": "fullstack"}
    metrics = {
        "fullstack": {
            "is_fullstack": True,
            "frontend_detected": {"present": True, "stack_guess": "React"},
            "backend_detected": {"present": True, "stack_guess": "FastAPI"},
        }
    }
    row = score_metric_rubric(rubric, metrics)
    assert row["score"] == 10.0
    assert row["weighted_score"] == 10.0
    assert "both frontend" in row["reason"].lower()


def test_fullstack_partial_frontend_only():
    rubric = {"id": "fullstack", "label": "Full-stack", "weight": 10, "weight_percent": 50, "max_score": 10, "metric": "fullstack"}
    metrics = {
        "fullstack": {
            "is_fullstack": False,
            "repo_type": "frontend",
            "frontend_detected": {"present": True, "stack_guess": "React"},
            "backend_detected": {"present": False, "stack_guess": None},
        }
    }
    row = score_metric_rubric(rubric, metrics)
    assert row["score"] == 2.0
    assert row["weighted_score"] == 2.0
    assert "only frontend" in row["reason"].lower()
    assert "React" in row["reason"]


def test_fullstack_partial_backend_only():
    rubric = {"id": "fullstack", "label": "Full-stack", "weight": 10, "weight_percent": 50, "max_score": 10, "metric": "fullstack"}
    metrics = {
        "fullstack": {
            "is_fullstack": False,
            "repo_type": "backend",
            "frontend_detected": {"present": False, "stack_guess": None},
            "backend_detected": {"present": True, "stack_guess": "Express"},
        }
    }
    row = score_metric_rubric(rubric, metrics)
    assert row["score"] == 2.0
    assert "only backend" in row["reason"].lower()
    assert "Express" in row["reason"]


def test_ai_usage_any_llm_full_score():
    rubric = {"id": "ai_usage", "label": "LLM", "weight": 4, "weight_percent": 20, "max_score": 10, "metric": "ai_usage"}
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
    row = score_metric_rubric(rubric, metrics)
    assert row["score"] == 10.0
    assert row["weighted_score"] == 4.0


def test_solution_fit_context_and_readme_full():
    rubric = {"id": "solution_fit", "label": "Fit", "weight": 2, "weight_percent": 10, "max_score": 10, "metric": "solution_fit"}
    metrics = {
        "solution_fit": {
            "status": "ok",
            "context_relevant": True,
            "relevance_score": 9,
            "alignment_score": 8.0,
            "reasoning": "Matches context",
            "readme": {
                "readme_quality_score": 8.0,
                "readme_has_local_setup": True,
                "reasoning": "Good README",
            },
        }
    }
    row = score_metric_rubric(rubric, metrics)
    assert row["score"] > 0
    assert row["weighted_score"] > 0


def test_aggregate_total_from_metrics():
    metrics = {
        "fullstack": {"is_fullstack": True, "frontend_detected": {"stack_guess": "React"}, "backend_detected": {"stack_guess": "FastAPI"}},
        "ai_usage": {"ai_integration_type": "none", "confidence": "high", "llm_providers": {"uses_llm": False}},
        "agent_analysis": {"status": "skipped"},
        "solution_fit": {
            "status": "ok",
            "context_relevant": True,
            "relevance_score": 8,
            "alignment_score": 8.0,
            "readme": {"readme_quality_score": 3.0, "readme_has_local_setup": False},
        },
    }
    access = RepoAccessInfo("o", "r", True, True, "main", None)
    scoring = aggregate_scores(metrics, access=access)
    assert scoring["total_score"] > 0
    assert scoring["max_total_score"] == 20.0
    assert scoring["rubrics"][0]["weight_percent"] == 50


def test_request_scoring_override(monkeypatch):
    monkeypatch.setenv(
        "RUBRIC_WEIGHTS_JSON",
        '[{"id":"fullstack","label":"FS","weight":50,"max_score":10,"metric":"fullstack"}]',
    )
    from app.config import get_settings

    get_settings.cache_clear()
    rubrics = resolve_rubrics()
    get_settings.cache_clear()
    assert len(rubrics) == 1
    assert rubrics[0]["weight"] == 50
