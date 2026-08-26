from __future__ import annotations

from app.metrics.agent_analysis import AgentAnalysisMetric
from app.metrics.ai_usage import AiUsageMetric
from app.metrics.base import Metric
from app.metrics.fullstack import FullstackMetric
from app.metrics.repo_health import RepoHealthMetric
from app.metrics.solution_fit import SolutionFitMetric

_REGISTRY: dict[str, Metric] = {}


def _register(metric: Metric) -> None:
    _REGISTRY[metric.name] = metric


def init_registry() -> None:
    if _REGISTRY:
        return
    for metric in (
        RepoHealthMetric(),
        FullstackMetric(),
        AiUsageMetric(),
        AgentAnalysisMetric(),
        SolutionFitMetric(),
    ):
        _register(metric)


def get_metric(name: str) -> Metric | None:
    init_registry()
    return _REGISTRY.get(name)


def all_metrics() -> list[Metric]:
    init_registry()
    return list(_REGISTRY.values())


def default_metric_names() -> list[str]:
    """Selectable metrics (excludes always-on which is forced separately)."""
    init_registry()
    return [m.name for m in _REGISTRY.values() if not m.always_on]


def resolve_requested(metrics: list[str] | None) -> list[str]:
    """Resolve request metrics: None → all selectable; always include always-on."""
    init_registry()
    always = [m.name for m in _REGISTRY.values() if m.always_on]
    selectable = default_metric_names()
    if metrics is None:
        chosen = list(selectable)
    else:
        unknown = [m for m in metrics if m not in _REGISTRY]
        if unknown:
            raise ValueError(f"Unknown metrics: {unknown}. Available: {list(_REGISTRY)}")
        chosen = list(dict.fromkeys(metrics))
    for name in always:
        if name not in chosen:
            chosen.append(name)
    return chosen


def catalogue() -> list[dict]:
    init_registry()
    return [
        {
            "name": m.name,
            "tier": m.tier,
            "description": m.description,
            "depends_on": m.depends_on,
            "always_on": m.always_on,
            "requires_context": getattr(m, "requires_context", False),
            "skippable_when": m.skippable_when,
            "output_schema": m.output_schema,
            "default_options": m.default_options,
        }
        for m in _REGISTRY.values()
    ]
