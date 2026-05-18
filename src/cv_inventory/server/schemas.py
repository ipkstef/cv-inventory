"""Pydantic models for request/response bodies."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Confidence = Literal["good", "fair", "poor"]


class IdentifyRequest(BaseModel):
    image_url: str
    set_id: int | None = None
    top_k: int = Field(default=5, ge=1, le=20)
    rotation_invariant: bool = True


class CandidateOut(BaseModel):
    product_id: int
    score: float
    name: str
    set_name: str
    set_abbr: str
    group_id: int
    collector_number: str | None
    rarity: str | None
    image_url: str


class IdentifyResponse(BaseModel):
    is_card_back: bool
    confidence: Confidence | None = None
    candidates: list[CandidateOut]


class IdentifyBatchItem(BaseModel):
    id: str
    image_url: str


class IdentifyBatchRequest(BaseModel):
    images: list[IdentifyBatchItem]
    set_id: int | None = None
    top_k: int = Field(default=5, ge=1, le=20)
    rotation_invariant: bool = True


class IdentifyBatchResult(BaseModel):
    id: str
    is_card_back: bool
    confidence: Confidence | None = None
    candidates: list[CandidateOut]
    error: str | None = None


class IdentifyBatchResponse(BaseModel):
    results: list[IdentifyBatchResult]


class ProductMatch(BaseModel):
    """Result shape for /search and similar product-lookup endpoints.

    Same shape as CandidateOut but without a score (no embedding ran).
    """

    product_id: int
    name: str
    set_name: str
    set_abbr: str
    group_id: int
    collector_number: str | None
    rarity: str | None
    image_url: str


class SearchResponse(BaseModel):
    results: list[ProductMatch]


class ResolveSkuRequest(BaseModel):
    printing: str
    condition: str
    language: str


class ExportRow(BaseModel):
    product_id: int
    printing: str
    condition: str
    language: str
    quantity: int = Field(ge=1)
    marketplace_price: float | None = None


class ExportRequest(BaseModel):
    rows: list[ExportRow]
