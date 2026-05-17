"""FastAPI app factory."""

from __future__ import annotations

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.responses import JSONResponse

from cv_inventory.image_fetch import FetchError, fetch_image
from cv_inventory.server.auth import require_bearer
from cv_inventory.server.schemas import (
    IdentifyBatchRequest,
    IdentifyBatchResponse,
    IdentifyRequest,
    IdentifyResponse,
)
from cv_inventory.server.state import AppState


def _candidate_dicts(candidates) -> list[dict]:
    return [c.__dict__ for c in candidates]


def create_app(state: AppState) -> FastAPI:
    app = FastAPI(title="cv-inventory", version="0.1.0")
    auth = require_bearer(state.api_key)
    app.state.deps = state

    @app.exception_handler(HTTPException)
    async def http_exc(_request, exc: HTTPException):
        code = "http_" + str(exc.status_code)
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": code, "message": exc.detail}},
        )

    router = APIRouter(dependencies=[Depends(auth)])

    @router.get("/health")
    async def health() -> dict:
        return {
            "status": "ok",
            "catalog_version": state.catalog_version,
            "catalog_size": len(state.catalog),
            "parquet_synced_at": state.parquet_synced_at.isoformat(),
        }

    @router.get("/sets")
    async def sets() -> dict:
        return {"sets": state.store.set_list()}

    @router.post("/identify", response_model=IdentifyResponse)
    async def identify(req: IdentifyRequest) -> dict:
        try:
            image = await fetch_image(req.image_url)
        except FetchError as e:
            raise HTTPException(status_code=400, detail=f"Could not fetch image: {e}") from e
        try:
            result = state.pipeline.identify(
                image=image,
                set_id=req.set_id,
                top_k=req.top_k,
                rotation_invariant=req.rotation_invariant,
            )
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        return {
            "is_card_back": result.is_card_back,
            "candidates": _candidate_dicts(result.candidates),
        }

    @router.post("/identify-batch", response_model=IdentifyBatchResponse)
    async def identify_batch(req: IdentifyBatchRequest) -> dict:
        import asyncio

        async def one(item):
            try:
                image = await fetch_image(item.image_url)
            except FetchError as e:
                return {
                    "id": item.id, "is_card_back": False, "candidates": [],
                    "error": f"fetch failed: {e}",
                }
            try:
                result = state.pipeline.identify(
                    image=image, set_id=req.set_id, top_k=req.top_k,
                    rotation_invariant=req.rotation_invariant,
                )
            except KeyError as e:
                return {
                    "id": item.id, "is_card_back": False, "candidates": [],
                    "error": str(e),
                }
            return {
                "id": item.id,
                "is_card_back": result.is_card_back,
                "candidates": _candidate_dicts(result.candidates),
                "error": None,
            }

        results = await asyncio.gather(*(one(i) for i in req.images))
        return {"results": list(results)}

    app.include_router(router)
    return app
