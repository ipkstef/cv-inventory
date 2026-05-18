"""End-to-end identification: PIL image -> top-K TCGplayer candidates with metadata."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
from collector_vision import NeuralEmbedder, rotate_card_180
from PIL import Image

from scan_and_identify.back_rejector import BackRejector
from scan_and_identify.set_index import SetIndex
from scan_and_identify.tcgplayer.store import TCGStore

Confidence = Literal["good", "fair", "poor"]


@dataclass(frozen=True)
class ConfidenceThresholds:
    """Thresholds for mapping (top-1 score, gap to top-2) -> confidence tier.

    Defaults calibrated against the 172-scan reference eval. Override per-deploy
    via env vars: SCAN_AND_IDENTIFY_CONF_{GOOD,POOR}_{SCORE,GAP}.
    """

    good_score: float = 0.55
    good_gap: float = 0.15
    poor_score: float = 0.45
    poor_gap: float = 0.05

    @classmethod
    def from_env(cls) -> ConfidenceThresholds:
        import os

        def _f(name: str, default: float) -> float:
            raw = os.environ.get(name)
            return float(raw) if raw else default

        return cls(
            good_score=_f("SCAN_AND_IDENTIFY_CONF_GOOD_SCORE", cls.good_score),
            good_gap=_f("SCAN_AND_IDENTIFY_CONF_GOOD_GAP", cls.good_gap),
            poor_score=_f("SCAN_AND_IDENTIFY_CONF_POOR_SCORE", cls.poor_score),
            poor_gap=_f("SCAN_AND_IDENTIFY_CONF_POOR_GAP", cls.poor_gap),
        )

    def classify(self, top_score: float, gap_to_next: float) -> Confidence:
        if top_score >= self.good_score and gap_to_next >= self.good_gap:
            return "good"
        if top_score < self.poor_score or gap_to_next < self.poor_gap:
            return "poor"
        return "fair"


# Module-level default for code that doesn't want to thread an instance through.
DEFAULT_THRESHOLDS = ConfidenceThresholds()


def classify_confidence(top_score: float, gap_to_next: float) -> Confidence:
    """Shorthand using default thresholds."""
    return DEFAULT_THRESHOLDS.classify(top_score, gap_to_next)


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
    confidence: Confidence | None = None  # None when candidates is empty


class IdentifyPipeline:
    def __init__(
        self,
        embedder: NeuralEmbedder,
        index: SetIndex,
        store: TCGStore,
        back_rejector: BackRejector,
        confidence_thresholds: ConfidenceThresholds | None = None,
    ) -> None:
        self._embedder = embedder
        self._index = index
        self._store = store
        self._back = back_rejector
        self._thresholds = confidence_thresholds or DEFAULT_THRESHOLDS

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
            return IdentifyResult(is_card_back=True, candidates=[], confidence=None)

        candidates = []
        for score, product_id in hits:
            p = self._store.product(product_id)
            if p is None:
                continue
            candidates.append(
                Candidate(
                    product_id=product_id,
                    score=float(score),
                    name=p["name"],
                    set_name=p["set_name"] or "",
                    set_abbr=p["set_abbr"] or "",
                    group_id=p["group_id"],
                    collector_number=p["collector_number"],
                    rarity=p["rarity"],
                    image_url=p["image_url"] or "",
                )
            )
        confidence: Confidence | None = None
        if candidates:
            gap = candidates[0].score - (candidates[1].score if len(candidates) > 1 else 0.0)
            confidence = self._thresholds.classify(candidates[0].score, gap)
        return IdentifyResult(is_card_back=False, candidates=candidates, confidence=confidence)
