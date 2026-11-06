import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core import all_models  # noqa: F401  (registers every table)
from app.modules.analysis.pipeline import fail_interrupted_runs
from app.modules.analysis.router import router as analysis_router
from app.modules.audit.router import router as audit_router
from app.modules.auth.router import bootstrap_admin
from app.modules.auth.router import router as auth_router
from app.modules.ingestion.router import router as ingestion_router
from app.modules.projects.router import router as projects_router
from app.modules.reporting.router import router as reporting_router
from app.modules.requirements.router import router as requirements_router
from app.modules.review.router import router as review_router

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    bootstrap_admin()
    fail_interrupted_runs()
    yield


app = FastAPI(title="ChangeLens AI", version="1.0.0", lifespan=lifespan)

for router in (
    auth_router,
    projects_router,
    ingestion_router,
    requirements_router,
    analysis_router,
    review_router,
    reporting_router,
    audit_router,
):
    app.include_router(router)


@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok"}
