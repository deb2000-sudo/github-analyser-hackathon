from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    host: str = "0.0.0.0"
    port: int = 8000

    # GCP — .env uses GOOGLE_CLOUD_*; Cloud Run may still set GCP_*
    gcp_project_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "GOOGLE_CLOUD_PROJECT",
            "GCP_PROJECT_ID",
            "FIREBASE_PROJECT_ID",
        ),
    )
    gcp_location: str = Field(
        default="us-central1",
        validation_alias=AliasChoices("GOOGLE_CLOUD_LOCATION", "GCP_LOCATION"),
    )

    # Firebase Admin — individual SA fields (local) or a JSON blob
    firebase_project_id: str | None = None
    firebase_private_key_id: str | None = None
    firebase_private_key: str | None = None
    firebase_client_email: str | None = None
    firebase_client_id: str | None = None
    firebase_database_url: str | None = None
    firebase_web_api_key: str | None = None
    firebase_credentials_json: str | None = None

    # Isolated collection for this analyser (not shared with other hackathon apps)
    firestore_collection_jobs: str = "githubanalysis_jobs"

    # Vertex AI Gemini — .env uses GEMINI_MODEL
    vertex_model: str = Field(
        default="gemini-2.5-flash",
        validation_alias=AliasChoices("GEMINI_MODEL", "VERTEX_MODEL"),
    )
    vertex_enabled: bool = True

    github_token: str | None = None
    hackathon_start: str | None = None
    hackathon_end: str | None = None
    rubric_weights_json: str | None = Field(default=None, validation_alias="RUBRIC_WEIGHTS_JSON")

    @field_validator("firebase_private_key")
    @classmethod
    def _normalize_private_key(cls, value: str | None) -> str | None:
        if not value:
            return value
        return value.replace("\\n", "\n")

    @property
    def llm_enabled(self) -> bool:
        return bool(self.vertex_enabled and self.vertex_project_id)

    @property
    def firestore_project_id(self) -> str | None:
        """Firebase / Firestore project."""
        return self.firebase_project_id or self.gcp_project_id

    @property
    def vertex_project_id(self) -> str | None:
        """Vertex AI project (ADC). Can differ from Firebase locally."""
        return self.gcp_project_id or self.firebase_project_id

    @property
    def resolved_project_id(self) -> str | None:
        return self.vertex_project_id

    def service_account_info(self) -> dict[str, Any] | None:
        """Build a Google service-account dict from .env fields or JSON."""
        if self.firebase_credentials_json:
            info = json.loads(self.firebase_credentials_json)
            if isinstance(info, dict):
                return info
            return None
        if self.firebase_client_email and self.firebase_private_key:
            project = self.firestore_project_id
            return {
                "type": "service_account",
                "project_id": project,
                "private_key_id": self.firebase_private_key_id or "",
                "private_key": self.firebase_private_key,
                "client_email": self.firebase_client_email,
                "client_id": self.firebase_client_id or "",
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                "client_x509_cert_url": (
                    "https://www.googleapis.com/robot/v1/metadata/x509/"
                    f"{self.firebase_client_email}"
                ),
                "universe_domain": "googleapis.com",
            }
        return None

    def vertex_credentials(self) -> Any | None:
        """User/Cloud Run ADC for Vertex. Never the Firebase Admin service account."""
        import google.auth

        creds, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        email = (getattr(creds, "service_account_email", None) or "").lower()
        if email.startswith("firebase-adminsdk"):
            raise RuntimeError(
                "Vertex AI resolved the Firebase Admin service account, which lacks "
                "aiplatform.endpoints.predict. Run: gcloud auth application-default login"
            )
        return creds


@lru_cache
def get_settings() -> Settings:
    return Settings()
