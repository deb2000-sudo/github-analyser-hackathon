from __future__ import annotations

import json
from typing import Any

import firebase_admin
from firebase_admin import credentials, firestore

from app.config import get_settings

_app: firebase_admin.App | None = None


def init_firebase() -> firebase_admin.App:
    """Initialize Firebase Admin once (Firestore via ADC or explicit SA JSON)."""
    global _app
    if _app is not None:
        return _app
    if firebase_admin._apps:  # type: ignore[attr-defined]
        _app = firebase_admin.get_app()
        return _app

    settings = get_settings()
    options: dict[str, Any] = {}
    if settings.gcp_project_id:
        options["projectId"] = settings.gcp_project_id

    if settings.firebase_credentials_json:
        info = json.loads(settings.firebase_credentials_json)
        cred = credentials.Certificate(info)
        _app = firebase_admin.initialize_app(cred, options or None)
    else:
        # Application Default Credentials (Cloud Run / gcloud ADC)
        try:
            cred = credentials.ApplicationDefault()
            _app = firebase_admin.initialize_app(cred, options or None)
        except Exception:
            # Last resort: initialize without explicit cred (uses env project)
            _app = firebase_admin.initialize_app(options=options or None)
    return _app


def get_firestore() -> firestore.Client:
    init_firebase()
    return firestore.client()
