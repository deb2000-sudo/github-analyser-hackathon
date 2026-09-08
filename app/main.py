from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.api.routes import router
from app.metrics.registry import init_registry


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Bind-first: do not call Firebase/Vertex here. Cloud Run health-checks the
    # listening port before the first request; a hung Google client would fail deploy.
    init_registry()
    yield


app = FastAPI(
    title="Repo Analysis Microservice",
    description="Pluggable GitHub repo analysis — Vertex AI Gemini, Firestore.",
    version=__version__,
    lifespan=lifespan,
)
app.include_router(router)
