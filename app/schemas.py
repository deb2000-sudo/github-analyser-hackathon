from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.github.validation import (
    InvalidGithubUrlError,
    UnsupportedGithubUrlError,
    normalize_github_repo_url,
)

MetricName = Literal[
    "fullstack", "frontend_backend", "ai_usage", "agent_analysis", "repo_health", "solution_fit"
]
JobStatusLiteral = Literal["queued", "running", "succeeded", "failed"]
Confidence = Literal["low", "medium", "high"]
AiIntegrationType = Literal["none", "wrapper", "rag", "agentic"]


class RubricDefinition(BaseModel):
    id: str
    label: str | None = None
    weight: float = Field(default=0, ge=0)
    max_score: float = Field(default=10, gt=0)
    metric: str | None = None


class AiEvidenceScoringConfig(BaseModel):
    """Optional overrides for the 0–100 AI integration evidence scale."""

    points: dict[str, int] | None = None
    penalties: dict[str, int] | None = None


class ScoringConfig(BaseModel):
    rubrics: list[RubricDefinition] | None = None
    ai_evidence: AiEvidenceScoringConfig | None = None


class SubmissionContext(BaseModel):
    """Free-form project context paragraph used to ground repository evaluation."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "provided_context": (
                    "This project is a multi-agent LangGraph study planner that uses RAG over "
                    "course materials and Gemini to help students build personalized study schedules."
                ),
                "rubrics": ["Uses an LLM", "Has real agent orchestration", "Full-stack demo"],
            }
        }
    )

    provided_context: str = Field(
        ...,
        min_length=1,
        description="Plain-text paragraph describing the project — what it is and what it should do.",
    )
    track: str | None = None
    rubrics: list[str] | None = Field(
        default=None,
        description="Optional judging rubrics / must-haves the LLM should check against.",
    )
    scoring: ScoringConfig | None = Field(
        default=None,
        description="Optional per-request rubric weight overrides.",
    )
    extra: dict[str, Any] = Field(
        default_factory=dict,
        description="Free-form organizer fields (theme, constraints, etc.).",
    )


class AnalyzeOptions(BaseModel):
    agent_analysis: dict[str, Any] = Field(default_factory=dict)
    ai_usage: dict[str, Any] = Field(default_factory=dict)
    fullstack: dict[str, Any] = Field(default_factory=dict)
    frontend_backend: dict[str, Any] = Field(default_factory=dict)
    repo_health: dict[str, Any] = Field(default_factory=dict)
    solution_fit: dict[str, Any] = Field(default_factory=dict)
    scoring: ScoringConfig | None = None


class AnalyzeRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "github_url": "https://github.com/owner/repo",
                "context": {
                    "provided_context": (
                        "This project is a multi-agent LangGraph study planner that uses RAG over "
                        "course materials and Gemini to help students build personalized study schedules."
                    ),
                    "rubrics": ["Uses an LLM", "Has real agent orchestration", "Full-stack demo"],
                },
            }
        }
    )

    github_url: str
    context: SubmissionContext = Field(
        ...,
        description="Required — includes `provided_context` paragraph for solution fit evaluation.",
    )
    metrics: list[str] | None = Field(
        default=None,
        description="Ignored for now — all metrics are always evaluated.",
    )
    options: AnalyzeOptions | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_legacy_context_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        ctx = data.get("context")
        if isinstance(ctx, dict):
            legacy = [
                k
                for k in ("problem_statement", "solution_description", "team_name", "text")
                if k in ctx
            ]
            if legacy:
                raise ValueError(
                    "Use context.provided_context (single combined string). "
                    f"Unsupported fields: {legacy}"
                )
        return data

    @field_validator("github_url")
    @classmethod
    def validate_github_url(cls, v: str) -> str:
        try:
            return normalize_github_repo_url(v)
        except InvalidGithubUrlError as exc:
            raise ValueError(str(exc)) from exc
        except UnsupportedGithubUrlError as exc:
            raise ValueError(str(exc)) from exc


class BatchAnalyzeRequest(BaseModel):
    items: list[AnalyzeRequest]


class JobCreatedResponse(BaseModel):
    job_id: str
    status: JobStatusLiteral
    metrics_requested: list[str]


class BatchAnalyzeResponse(BaseModel):
    jobs: list[JobCreatedResponse]


class EvidenceRecord(BaseModel):
    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "example": {
                "id": "ev_123",
                "rule_id": "AI.DYNAMIC_INPUT",
                "file": "backend/chat.py",
                "line": 32,
                "symbol": "chat",
                "description": "Request message reaches Gemini model invocation",
            }
        },
    )

    id: str | None = None
    rule_id: str | None = None
    file: str | None = None
    line: int | None = None
    symbol: str | None = None
    description: str | None = None


class AiResultSummary(BaseModel):
    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "example": {
                "detected": True,
                "providers": ["gemini"],
                "integration_level": 6,
                "classification": "genuine_ai_integration",
                "model_invocation_detected": True,
                "user_input_reaches_model": True,
                "model_output_used": True,
                "hardcoded_response_detected": False,
                "confidence": 0.95,
            }
        },
    )

    detected: bool = False
    providers: list[str] = Field(default_factory=list)
    integration_level: int = 0
    classification: str = "no_ai"
    model_invocation_detected: bool = False
    user_input_reaches_model: bool = False
    model_output_used: bool = False
    hardcoded_response_detected: bool = False
    confidence: float = 0.0


class AnalysisResult(BaseModel):
    """Explainable job result. Legacy keys (`repo`, `verdict`, `analysis`, `context`) are allowed."""

    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "example": {
                "access": {"is_public": True},
                "repository": {"owner": "owner", "name": "repo"},
                "architecture": {"application_type": "full_stack"},
                "frontend_backend": {"connected": True},
                "ai": {
                    "detected": True,
                    "providers": ["gemini"],
                    "integration_level": 6,
                    "classification": "genuine_ai_integration",
                    "model_invocation_detected": True,
                    "user_input_reaches_model": True,
                    "model_output_used": True,
                    "hardcoded_response_detected": False,
                    "confidence": 0.95,
                },
                "agents": {"classification": "NO_AGENT"},
                "solution_fit": {"implementation_matches_claim": True},
                "metrics": {},
                "scoring": {"total_score": 0},
                "evidence": [
                    {
                        "id": "ev_123",
                        "rule_id": "AI.DYNAMIC_INPUT",
                        "file": "backend/chat.py",
                        "line": 32,
                        "symbol": "chat",
                        "description": "Request message reaches Gemini model invocation",
                    }
                ],
                "limitations": [
                    "Dynamic import could not be resolved",
                    "Frontend URL constructed at runtime",
                ],
                "metadata": {"version": "0.1.0", "uncertain_results_are_not_proven": True},
            }
        },
    )

    access: dict[str, Any] = Field(default_factory=dict)
    repository: dict[str, Any] = Field(default_factory=dict)
    architecture: dict[str, Any] = Field(default_factory=dict)
    frontend_backend: dict[str, Any] = Field(default_factory=dict)
    ai: AiResultSummary = Field(default_factory=AiResultSummary)
    agents: dict[str, Any] = Field(default_factory=dict)
    solution_fit: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)
    scoring: dict[str, Any] = Field(default_factory=dict)
    evidence: list[EvidenceRecord] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    repo: dict[str, Any] | None = None
    context: dict[str, Any] | None = None
    verdict: dict[str, Any] | None = None
    analysis: dict[str, Any] | None = None


class JobResponse(BaseModel):
    job_id: str
    status: JobStatusLiteral
    github_url: str
    metrics_requested: list[str]
    context: dict[str, Any] | None = None
    commit_sha: str | None = None
    result: AnalysisResult | None = None
    error: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class MetricCatalogueEntry(BaseModel):
    name: str
    tier: Literal["static", "llm"]
    description: str
    depends_on: list[str] = Field(default_factory=list)
    always_on: bool = False
    skippable_when: str | None = None
    requires_context: bool = False
    output_schema: dict[str, Any]
    default_options: dict[str, Any] = Field(default_factory=dict)


class MetricsCatalogueResponse(BaseModel):
    metrics: list[MetricCatalogueEntry]


class RubricCatalogueEntry(BaseModel):
    id: str
    label: str
    weight: float
    weight_percent: float | None = None
    max_score: float
    metric: str | None = None


class RubricsCatalogueResponse(BaseModel):
    rubrics: list[RubricCatalogueEntry]
    max_total_score: float


class HealthResponse(BaseModel):
    status: str
    llm_enabled: bool
    version: str
    worker_mode: Literal["cloud_run_job", "inline"] = "inline"
