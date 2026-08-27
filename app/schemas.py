from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.github.validation import (
    InvalidGithubUrlError,
    UnsupportedGithubUrlError,
    normalize_github_repo_url,
)

MetricName = Literal[
    "fullstack", "ai_usage", "agent_analysis", "repo_health", "solution_fit"
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


class ScoringConfig(BaseModel):
    rubrics: list[RubricDefinition] | None = None


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


class JobResponse(BaseModel):
    job_id: str
    status: JobStatusLiteral
    github_url: str
    metrics_requested: list[str]
    context: dict[str, Any] | None = None
    commit_sha: str | None = None
    result: dict[str, Any] | None = None
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
