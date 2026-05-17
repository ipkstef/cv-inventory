"""FastAPI app factory."""

from __future__ import annotations

from fastapi import APIRouter, Depends, FastAPI

from cv_inventory.server.auth import require_bearer
from cv_inventory.server.state import AppState


def create_app(state: AppState) -> FastAPI:
    app = FastAPI(title="cv-inventory", version="0.1.0")
    auth = require_bearer(state.api_key)
    app.state.deps = state

    router = APIRouter(dependencies=[Depends(auth)])

    @router.get("/health")
    async def health() -> dict:
        return {
            "status": "ok",
            "catalog_version": state.catalog_version,
            "catalog_size": len(state.catalog),
            "parquet_synced_at": state.parquet_synced_at.isoformat(),
        }

    app.include_router(router)
    return app
