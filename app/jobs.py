from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from google.cloud import firestore

from app.config import get_settings
from app.firebase_app import get_firestore


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_job_id() -> str:
    return uuid4().hex[:12]


class JobStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"


@dataclass
class Job:
    id: str
    status: str
    github_url: str
    metrics_requested: list[str] = field(default_factory=list)
    options: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    commit_sha: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def to_firestore(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "github_url": self.github_url,
            "metrics_requested": self.metrics_requested,
            "options": self.options,
            "context": self.context,
            "result": self.result,
            "error": self.error,
            "commit_sha": self.commit_sha,
            "created_at": self.created_at or utcnow(),
            "updated_at": self.updated_at or utcnow(),
        }

    @classmethod
    def from_firestore(cls, job_id: str, data: dict[str, Any]) -> Job:
        return cls(
            id=job_id,
            status=data.get("status", JobStatus.queued.value),
            github_url=data.get("github_url", ""),
            metrics_requested=list(data.get("metrics_requested") or []),
            options=dict(data.get("options") or {}),
            context=data.get("context"),
            result=data.get("result"),
            error=data.get("error"),
            commit_sha=data.get("commit_sha"),
            created_at=_as_dt(data.get("created_at")),
            updated_at=_as_dt(data.get("updated_at")),
        )


def _as_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return None


class JobStore:
    """Firestore-backed job persistence."""

    def __init__(self, collection: str | None = None):
        settings = get_settings()
        self._collection = collection or settings.firestore_collection_jobs
        self._db = get_firestore()

    def _col(self) -> firestore.CollectionReference:
        return self._db.collection(self._collection)

    def create(self, job: Job) -> Job:
        now = utcnow()
        job.created_at = now
        job.updated_at = now
        self._col().document(job.id).set(job.to_firestore())
        return job

    def get(self, job_id: str) -> Job | None:
        snap = self._col().document(job_id).get()
        if not snap.exists:
            return None
        return Job.from_firestore(job_id, snap.to_dict() or {})

    def update(self, job_id: str, **fields: Any) -> None:
        fields["updated_at"] = utcnow()
        self._col().document(job_id).update(fields)
