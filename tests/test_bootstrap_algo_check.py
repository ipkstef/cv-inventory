"""AppState.bootstrap_for_tests must hard-fail on catalogs without milo1+phash1."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from tests.fixtures.synthetic import write_synthetic_parquets


def _write_legacy_catalog(path: Path) -> None:
    """Write a catalog with the old milo1 algo_key and no name_phashes."""
    embeddings = np.random.default_rng(0).standard_normal((1, 128)).astype(np.float32)
    embeddings /= np.linalg.norm(embeddings, axis=1, keepdims=True)
    np.savez_compressed(
        path,
        embeddings=embeddings,
        card_ids=np.array(["1001"], dtype="<U36"),
        source="tcgplayer",
        embedder_spec=json.dumps({"kind": "neural", "algo_key": "milo1"}),
        built_at="2026-04-01T00:00:00Z",
    )


def test_bootstrap_rejects_legacy_catalog(tmp_path):
    from scan_and_identify.server.state import AppState

    parquet_dir = tmp_path / "parquets"
    write_synthetic_parquets(parquet_dir)
    catalog = tmp_path / "legacy.npz"
    _write_legacy_catalog(catalog)

    with pytest.raises(ValueError, match=r"milo1\+phash1"):
        AppState.bootstrap_for_tests(api_key="x", catalog_path=catalog, parquet_dir=parquet_dir)
