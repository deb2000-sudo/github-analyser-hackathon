from __future__ import annotations

from typing import Any

import firebase_admin
from firebase_admin import credentials, firestore

from app.config import get_settings

_app: firebase_admin.App | None = None


def init_firebase() -> firebase_admin.App:
    """Initialize Firebase Admin once (SA from .env locally, ADC on Cloud Run)."""
    global _app
    if _app is not None:
        return _app
    if firebase_admin._apps:  # type: ignore[attr-defined]
        _app = firebase_admin.get_app()
        return _app

    settings = get_settings()
    options: dict[str, Any] = {}
    project_id = settings.resolved_project_id
    if project_id:
        options["projectId"] = project_id
    if settings.firebase_database_url:
        options["databaseURL"] = settings.firebase_database_url

    info = settings.service_account_info()
    if info:
        cred = credentials.Certificate(info)
        _app = firebase_admin.initialize_app(cred, options or None)
    else:
        try:
            cred = credentials.ApplicationDefault()
            _app = firebase_admin.initialize_app(cred, options or None)
        except Exception:
            _app = firebase_admin.initialize_app(options=options or None)
    return _app


def get_firestore() -> firestore.Client:
    init_firebase()
    return firestore.client()
