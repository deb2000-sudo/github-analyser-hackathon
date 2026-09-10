"""Swappable LLM reasoning interface.

Gemini is one implementation. Deterministic code analysis answers
factual questions; a reasoner is only for semantic interpretation.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ReasoningRequest:
    """Curated evidence for a selective LLM call. Never the full repository."""

    metrics: list[str]
    questions: list[str]
    evidence: dict[str, Any]
    submission_context: dict[str, Any] | None = None
    reasons: list[str] = field(default_factory=list)


class LLMReasoner(ABC):
    """Semantic judge. Implementations must return structured JSON."""

    @property
    @abstractmethod
    def enabled(self) -> bool:
        """True when the backend can run a reasoning call."""

    @abstractmethod
    async def reason(self, request: ReasoningRequest) -> dict[str, Any]:
        """Answer only the supplied semantic questions from the evidence pack."""
