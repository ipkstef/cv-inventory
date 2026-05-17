"""Per-set sub-catalogs for hard set-lock filtering."""

from __future__ import annotations

import numpy as np
from collector_vision import Catalog

from cv_inventory.tcgplayer.store import TCGStore


class SetIndex:
    """Holds the full catalog plus one Catalog-shaped sub-view per group_id present."""

    def __init__(self, full: Catalog, by_set: dict[int, Catalog]) -> None:
        self._full = full
        self._by_set = by_set

    @classmethod
    def build(cls, catalog: Catalog, store: TCGStore) -> SetIndex:
        product_ids = [int(s) for s in catalog.card_ids]
        group_ids = np.array(
            [store.product(pid)["group_id"] if store.product(pid) else -1 for pid in product_ids],
            dtype=np.int64,
        )

        by_set: dict[int, Catalog] = {}
        for group_id in np.unique(group_ids):
            if group_id == -1:
                continue
            mask = group_ids == group_id
            sub_card_ids = [str(product_ids[i]) for i, m in enumerate(mask) if m]
            sub = Catalog(
                embeddings=catalog.embeddings[mask],
                card_ids=sub_card_ids,
                source=catalog.source,
                embedder_spec=catalog.embedder_spec,
                oracle_ids=None,
            )
            by_set[int(group_id)] = sub
        return cls(full=catalog, by_set=by_set)

    def search(
        self, embedding: np.ndarray, set_id: int | None, top_k: int
    ) -> list[tuple[float, int]]:
        if set_id is None:
            target = self._full
        else:
            if set_id not in self._by_set:
                raise KeyError(f"Unknown set_id {set_id}")
            target = self._by_set[set_id]
        return [(score, int(cid)) for score, cid in target.search(embedding, top_k=top_k)]
