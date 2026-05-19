"""FastAPI app factory."""

from __future__ import annotations

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.responses import JSONResponse, Response

from scan_and_identify.image_fetch import FetchError, fetch_image
from scan_and_identify.server.auth import require_bearer
from scan_and_identify.server.schemas import (
    ExportRequest,
    IdentifyBatchRequest,
    IdentifyBatchResponse,
    IdentifyRequest,
    IdentifyResponse,
    ResolveSkuRequest,
    SearchResponse,
)
from scan_and_identify.server.state import AppState
from scan_and_identify.tcgplayer.seller_csv import MergePriceConflict, build_seller_csv


def _candidate_dicts(candidates) -> list[dict]:
    return [c.__dict__ for c in candidates]


def create_app(state: AppState) -> FastAPI:
    app = FastAPI(title="scan-and-identify", version="0.1.0")
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
            "catalog_built_at": state.catalog_built_at,
            "catalog_size": len(state.catalog),
            "parquet_synced_at": state.parquet_synced_at.isoformat(),
        }

    @router.get("/sets")
    async def sets() -> dict:
        return {"sets": state.store.set_list()}

    @router.get("/search", response_model=SearchResponse)
    async def search(
        name: str | None = None,
        collector_number: str | None = None,
        set_id: int | None = None,
        limit: int = 20,
    ) -> dict:
        if not name and not collector_number:
            raise HTTPException(
                status_code=400,
                detail="At least one of 'name' or 'collector_number' is required",
            )
        if limit < 1 or limit > 100:
            raise HTTPException(status_code=400, detail="limit must be between 1 and 100")
        results = state.store.search_products(
            name=name,
            collector_number=collector_number,
            set_id=set_id,
            limit=limit,
        )
        # store.product() returns "tcgplayer_url" + "clean_name" + "is_sealed" — strip to ProductMatch shape.
        out = []
        for p in results:
            out.append(
                {
                    "product_id": p["product_id"],
                    "name": p["name"],
                    "set_name": p["set_name"] or "",
                    "set_abbr": p["set_abbr"] or "",
                    "group_id": p["group_id"],
                    "collector_number": p["collector_number"],
                    "rarity": p["rarity"],
                    "image_url": p["image_url"] or "",
                }
            )
        return {"results": out}

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
            "confidence": result.confidence,
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
                    "id": item.id,
                    "is_card_back": False,
                    "confidence": None,
                    "candidates": [],
                    "error": f"fetch failed: {e}",
                }
            try:
                result = state.pipeline.identify(
                    image=image,
                    set_id=req.set_id,
                    top_k=req.top_k,
                    rotation_invariant=req.rotation_invariant,
                )
            except KeyError as e:
                return {
                    "id": item.id,
                    "is_card_back": False,
                    "confidence": None,
                    "candidates": [],
                    "error": str(e),
                }
            return {
                "id": item.id,
                "is_card_back": result.is_card_back,
                "confidence": result.confidence,
                "candidates": _candidate_dicts(result.candidates),
                "error": None,
            }

        results = await asyncio.gather(*(one(i) for i in req.images))
        return {"results": list(results)}

    @router.get("/products/{product_id}")
    async def get_product(product_id: int) -> dict:
        p = state.store.product(product_id)
        if p is None:
            raise HTTPException(status_code=404, detail=f"Unknown product_id {product_id}")
        p["skus"] = state.store.skus_for_product(product_id)
        return p

    @router.post("/products/{product_id}/resolve-sku")
    async def resolve_sku(product_id: int, req: ResolveSkuRequest) -> dict:
        if state.store.product(product_id) is None:
            raise HTTPException(status_code=404, detail=f"Unknown product_id {product_id}")
        sku = state.store.resolve_sku(
            product_id,
            printing=req.printing,
            condition=req.condition,
            language=req.language,
        )
        if sku is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"No SKU for product {product_id} with printing={req.printing}, "
                    f"condition={req.condition}, language={req.language}"
                ),
            )
        return {
            "sku_id": sku["sku_id"],
            "market_price": sku["market_price"],
            "low_price": sku["low_price"],
            "mid_price": sku["mid_price"],
            "high_price": sku["high_price"],
            "direct_low_price": sku["direct_low_price"],
        }

    @router.post("/export/tcgplayer-csv")
    async def export_csv(req: ExportRequest) -> Response:
        rows = [r.model_dump() for r in req.rows]
        formula = req.price_formula.model_dump() if req.price_formula else None
        try:
            body = build_seller_csv(
                state.store,
                rows,
                merge_duplicates=req.merge_duplicates,
                price_formula=formula,
            )
        except MergePriceConflict as e:
            return JSONResponse(
                status_code=400,
                content={
                    "error": {
                        "code": "merge_price_conflict",
                        "message": str(e),
                        "conflicts": e.conflicts,
                    }
                },
            )
        return Response(
            content=body,
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="tcgplayer-export.csv"'},
        )

    app.include_router(router)
    return app
