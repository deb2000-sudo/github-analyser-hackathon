from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    host: str = "0.0.0.0"
    port: int = 8000

    # GCP project (ADC on Cloud Run; set explicitly for local)
    gcp_project_id: str | None = None
    gcp_location: str = "us-central1"

    # Firebase / Firestore
    # Leave empty to use default Firestore in GCP_PROJECT_ID.
    # Set FIREBASE_CREDENTIALS_JSON to a service-account JSON string for local/dev,
    # or rely on GOOGLE_APPLICATION_CREDENTIALS / ADC.
    firebase_credentials_json: str | None = None
    firestore_collection_jobs: str = "analysis_jobs"

    # GCS — GitHub snapshot cache
    gcs_bucket: str | None = None
    gcs_cache_prefix: str = "github-cache"
    github_cache_ttl_seconds: int = 86400

    # Vertex AI Gemini
    vertex_model: str = "gemini-2.0-flash"
    # llm_enabled when project is set (Vertex uses ADC, not an API key)
    vertex_enabled: bool = True

    github_token: str | None = None
    hackathon_start: str | None = None
    hackathon_end: str | None = None

    @property
    def llm_enabled(self) -> bool:
        return bool(self.vertex_enabled and self.gcp_project_id)

    @property
    def gcs_enabled(self) -> bool:
        return bool(self.gcs_bucket)


@lru_cache
def get_settings() -> Settings:
    return Settings()
