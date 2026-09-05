from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.api.routes import router
from app.config import get_settings
from app.firebase_app import init_firebase
from app.metrics.registry import init_registry


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_registry()
    settings = get_settings()
    # Firebase optional at import-time for unit tests; init when project/SA is configured
    if settings.resolved_project_id or settings.service_account_info():
        try:
            init_firebase()
        except Exception:
            # Allow boot without credentials for /health + /metrics in CI
            pass
    yield


app = FastAPI(
    title="Repo Analysis Microservice",
    description="Pluggable GitHub repo analysis — Vertex AI Gemini, Firestore.",
    version=__version__,
    lifespan=lifespan,
)
app.include_router(router)
