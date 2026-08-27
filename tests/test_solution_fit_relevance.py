"""Solution fit relevance gate tests."""

from __future__ import annotations

from app.metrics.solution_fit_normalize import normalize_solution_fit
from app.scoring.aggregator import score_metric_rubric


def test_unrelated_repo_zero_alignment():
    section = normalize_solution_fit(
        {
            "context_relevant": False,
            "relevance_score": 0,
            "alignment_score": 6,
            "implements_claimed_solution": True,
            "reasoning": "Uptime Kuma is a monitoring tool, not a study planner.",
        }
    )
    assert section["context_relevant"] is False
    assert section["alignment_score"] == 0.0
    assert section["implements_claimed_solution"] is False


def test_low_relevance_zero_alignment():
    section = normalize_solution_fit(
        {
            "relevance_score": 2,
            "alignment_score": 8,
            "implements_claimed_solution": True,
        }
    )
    assert section["context_relevant"] is False
    assert section["alignment_score"] == 0.0


def test_rubric_scores_zero_when_not_relevant():
    rubric = {"id": "solution_fit", "label": "Fit", "weight": 2, "weight_percent": 10, "max_score": 10, "metric": "solution_fit"}
    metrics = {
        "solution_fit": {
            "status": "ok",
            "context_relevant": False,
            "relevance_score": 1,
            "alignment_score": 0,
            "implements_claimed_solution": False,
            "reasoning": "Different product domain.",
            "readme": {"readme_quality_score": 9, "readme_has_local_setup": True, "reasoning": "Great README"},
        }
    }
    row = score_metric_rubric(rubric, metrics)
    assert row["weighted_score"] == 0.0
    assert "NOT relevant" in row["reason"]


def test_rubric_no_points_for_readme_alone():
    rubric = {"id": "solution_fit", "label": "Fit", "weight": 2, "weight_percent": 10, "max_score": 10, "metric": "solution_fit"}
    metrics = {
        "solution_fit": {
            "status": "ok",
            "context_relevant": True,
            "relevance_score": 8,
            "alignment_score": 2,
            "reasoning": "Related domain but missing core features.",
            "readme": {"readme_quality_score": 10, "readme_has_local_setup": True, "reasoning": "Excellent README"},
        }
    }
    row = score_metric_rubric(rubric, metrics)
    assert row["weighted_score"] == 0.0
