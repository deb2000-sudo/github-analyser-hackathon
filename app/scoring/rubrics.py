from __future__ import annotations

import json
from typing import Any

from app.config import Settings, get_settings

# Weights sum to 20 (50% + 20% + 20% + 10%). total_score is out of 20.
DEFAULT_RUBRICS: list[dict[str, Any]] = [
    {
        "id": "fullstack",
        "label": "Full-stack demo",
        "weight": 10,
        "weight_percent": 50,
        "max_score": 10,
        "metric": "fullstack",
    },
    {
        "id": "ai_usage",
        "label": "Uses an LLM",
        "weight": 4,
        "weight_percent": 20,
        "max_score": 10,
        "metric": "ai_usage",
    },
    {
        "id": "agent_analysis",
        "label": "Real agent orchestration",
        "weight": 4,
        "weight_percent": 20,
        "max_score": 10,
        "metric": "agent_analysis",
    },
    {
        "id": "solution_fit",
        "label": "Context fit & README quality",
        "weight": 2,
        "weight_percent": 10,
        "max_score": 10,
        "metric": "solution_fit",
    },
]


def _coerce_rubric(item: dict[str, Any]) -> dict[str, Any]:
    weight = float(item.get("weight", 0))
    return {
        "id": str(item["id"]),
        "label": str(item.get("label") or item["id"]),
        "weight": weight,
        "weight_percent": float(item.get("weight_percent", weight / 20 * 100)),
        "max_score": float(item.get("max_score", 10)),
        "metric": item.get("metric"),
    }


def load_default_rubrics(settings: Settings | None = None) -> list[dict[str, Any]]:
    settings = settings or get_settings()
    raw = settings.rubric_weights_json
    if not raw:
        return [dict(r) for r in DEFAULT_RUBRICS]
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid RUBRIC_WEIGHTS_JSON: {exc}") from exc
    if not isinstance(data, list):
        raise ValueError("RUBRIC_WEIGHTS_JSON must be a JSON array")
    return [_coerce_rubric(item) for item in data if isinstance(item, dict) and item.get("id")]


def resolve_rubrics(
    *,
    settings: Settings | None = None,
    request_scoring: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Env defaults merged with per-request overrides (matched by id)."""
    base = {r["id"]: r for r in load_default_rubrics(settings)}
    if request_scoring:
        overrides = request_scoring.get("rubrics") or []
        if isinstance(overrides, list):
            for item in overrides:
                if isinstance(item, dict) and item.get("id"):
                    merged = {**base.get(item["id"], {}), **item}
                    base[item["id"]] = _coerce_rubric(merged)
    return list(base.values())


def rubric_catalogue(settings: Settings | None = None) -> list[dict[str, Any]]:
    return load_default_rubrics(settings)
