from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal

from app.github.client import RepoSnapshot


@dataclass
class MetricContext:
    snapshot: RepoSnapshot
    options: dict[str, Any] = field(default_factory=dict)
    prior_results: dict[str, Any] = field(default_factory=dict)
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass
class MetricResult:
    name: str
    status: Literal["ok", "skipped", "error"]
    data: dict[str, Any] | None = None
    reason: str | None = None


class Metric(ABC):
    name: str
    tier: Literal["static", "llm"]
    description: str
    depends_on: list[str] = []
    always_on: bool = False
    requires_context: bool = False
    skippable_when: str | None = None
    output_schema: dict[str, Any] = {}
    default_options: dict[str, Any] = {}

    @abstractmethod
    async def run(self, ctx: MetricContext) -> MetricResult:
        ...
