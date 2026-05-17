"""End-to-end identification: PIL image -> top-K TCGplayer candidates with metadata."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image

from collector_vision import NeuralEmbedder, rotate_card_180
from cv_inventory.back_rejector import BackRejector
from cv_inventory.set_index import SetIndex
from cv_inventory.tcgplayer.store import TCGStore


@dataclass
class Candidate:
    product_id: int
    score: float
    name: str
    set_name: str
    set_abbr: str
    group_id: int
    collector_number: str | None
    rarity: str | None
    image_url: str


@dataclass
class IdentifyResult:
    is_card_back: bool
    candidates: list[Candidate]


class IdentifyPipeline:
    def __init__(
        self,
        embedder: NeuralEmbedder,
        index: SetIndex,
        store: TCGStore,
        back_rejector: BackRejector,
    ) -> None:
        self._embedder = embedder
        self._index = index
        self._store = store
        self._back = back_rejector

    def identify(
        self,
        image: Image.Image,
        set_id: int | None,
        top_k: int,
        rotation_invariant: bool,
    ) -> IdentifyResult:
        img = image.convert("RGB").resize((448, 448))

        if rotation_invariant:
            rotated = rotate_card_180(img)
            emb_a = np.asarray(self._embedder.embed(img), dtype=np.float32)
            emb_b = np.asarray(self._embedder.embed(rotated), dtype=np.float32)
            hits_a = self._index.search(emb_a, set_id=set_id, top_k=top_k)
            hits_b = self._index.search(emb_b, set_id=set_id, top_k=top_k)
            if hits_b and (not hits_a or hits_b[0][0] > hits_a[0][0]):
                emb, hits = emb_b, hits_b
            else:
                emb, hits = emb_a, hits_a
        else:
            emb = np.asarray(self._embedder.embed(img), dtype=np.float32)
            hits = self._index.search(emb, set_id=set_id, top_k=top_k)

        top_score = hits[0][0] if hits else 0.0
        if self._back.is_back(embedding=emb, top_catalog_score=top_score):
            return IdentifyResult(is_card_back=True, candidates=[])

        candidates = []
        for score, product_id in hits:
            p = self._store.product(product_id)
            if p is None:
                continue
            candidates.append(Candidate(
                product_id=product_id,
                score=float(score),
                name=p["name"],
                set_name=p["set_name"] or "",
                set_abbr=p["set_abbr"] or "",
                group_id=p["group_id"],
                collector_number=p["collector_number"],
                rarity=p["rarity"],
                image_url=p["image_url"] or "",
            ))
        return IdentifyResult(is_card_back=False, candidates=candidates)
