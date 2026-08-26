from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

MetricName = Literal[
    "fullstack", "ai_usage", "agent_analysis", "repo_health", "solution_fit"
]
JobStatusLiteral = Literal["queued", "running", "succeeded", "failed"]
Confidence = Literal["low", "medium", "high"]
AiIntegrationType = Literal["none", "wrapper", "rag", "agentic"]


class SubmissionContext(BaseModel):
    """Hackathon problem + claimed solution — used to ground evaluation."""

    problem_statement: str = Field(
        ...,
        min_length=1,
        description="What the hackathon asked teams to build / solve.",
    )
    solution_description: str | None = Field(
        default=None,
        description="Team's stated approach / solution write-up from the submission form.",
    )
    team_name: str | None = None
    track: str | None = None
    rubrics: list[str] | None = Field(
        default=None,
        description="Optional judging rubrics / must-haves the LLM should check against.",
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


class AnalyzeRequest(BaseModel):
    github_url: str
    context: SubmissionContext | None = Field(
        default=None,
        description="Problem statement + solution context for hackathon evaluation.",
    )
    metrics: list[str] | None = None
    options: AnalyzeOptions | None = None

    @field_validator("github_url")
    @classmethod
    def validate_github_url(cls, v: str) -> str:
        v = v.strip().rstrip("/")
        if "github.com" not in v:
            raise ValueError("github_url must be a GitHub repository URL")
        return v


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


class HealthResponse(BaseModel):
    status: str
    llm_enabled: bool
    version: str
