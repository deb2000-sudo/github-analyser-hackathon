"""Settings aliases match the hackathon .env key names."""

from __future__ import annotations

from app.config import Settings, get_settings


def test_env_aliases_map_to_settings(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "nxt-acad-hackathon")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-flash")
    monkeypatch.setenv("FIRESTORE_COLLECTION_JOBS", "githubanalysis_jobs")
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "nxt-acad-hackathon")
    monkeypatch.setenv("FIREBASE_CLIENT_EMAIL", "sa@example.com")
    monkeypatch.setenv(
        "FIREBASE_PRIVATE_KEY",
        "-----BEGIN PRIVATE KEY-----\\nABC\\n-----END PRIVATE KEY-----\\n",
    )
    get_settings.cache_clear()
    settings = Settings(_env_file=None)

    assert settings.gcp_project_id == "nxt-acad-hackathon"
    assert settings.gcp_location == "us-central1"
    assert settings.vertex_model == "gemini-2.5-flash"
    assert settings.firestore_collection_jobs == "githubanalysis_jobs"
    assert settings.llm_enabled is True
    info = settings.service_account_info()
    assert info is not None
    assert info["client_email"] == "sa@example.com"
    assert "\n" in info["private_key"]
    get_settings.cache_clear()


def test_firebase_and_vertex_can_use_different_projects(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "nxt-acad-hackathon")
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "github-analyser-786")
    get_settings.cache_clear()
    settings = Settings(_env_file=None)

    assert settings.vertex_project_id == "nxt-acad-hackathon"
    assert settings.firestore_project_id == "github-analyser-786"
    get_settings.cache_clear()


def test_legacy_gcp_aliases_still_work(monkeypatch):
    monkeypatch.setenv("GCP_PROJECT_ID", "legacy-project")
    monkeypatch.setenv("GCP_LOCATION", "europe-west1")
    monkeypatch.setenv("VERTEX_MODEL", "gemini-2.0-flash")
    get_settings.cache_clear()
    settings = Settings(_env_file=None)

    assert settings.gcp_project_id == "legacy-project"
    assert settings.gcp_location == "europe-west1"
    assert settings.vertex_model == "gemini-2.0-flash"
    get_settings.cache_clear()
