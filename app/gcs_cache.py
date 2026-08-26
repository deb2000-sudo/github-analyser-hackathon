from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from google.cloud import storage

from app.config import Settings, get_settings


class GcsCache:
    """Cache raw GitHub payloads in GCS keyed by repo + commit SHA."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self._client: storage.Client | None = None
        if self.settings.gcs_enabled:
            self._client = storage.Client(project=self.settings.gcp_project_id)

    @property
    def enabled(self) -> bool:
        return self._client is not None and bool(self.settings.gcs_bucket)

    def _blob_name(self, cache_key: str) -> str:
        digest = hashlib.sha256(cache_key.encode()).hexdigest()[:32]
        safe = cache_key.replace("/", "_").replace("@", "_at_")[:120]
        prefix = self.settings.gcs_cache_prefix.rstrip("/")
        return f"{prefix}/{safe}_{digest}.json"

    def get(self, cache_key: str) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        assert self._client and self.settings.gcs_bucket
        bucket = self._client.bucket(self.settings.gcs_bucket)
        blob = bucket.blob(self._blob_name(cache_key))
        if not blob.exists():
            return None
        blob.reload()
        updated = blob.updated
        if updated:
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - updated).total_seconds()
            if age > self.settings.github_cache_ttl_seconds:
                return None
        raw = blob.download_as_text()
        return json.loads(raw)

    def set(self, cache_key: str, payload: dict[str, Any]) -> None:
        if not self.enabled:
            return
        assert self._client and self.settings.gcs_bucket
        bucket = self._client.bucket(self.settings.gcs_bucket)
        blob = bucket.blob(self._blob_name(cache_key))
        blob.upload_from_string(
            json.dumps(payload),
            content_type="application/json",
        )
