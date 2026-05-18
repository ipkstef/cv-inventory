"""Unit tests for the catalog-build primitives.

These exercise the primitives independently from the orchestrators
(:func:`download_only`, :func:`build_catalog`), which are covered separately
by tests/test_catalog_build.py.
"""

from __future__ import annotations

import io

import numpy as np
import pandas as pd
import pytest
from PIL import Image

from scan_and_identify.catalog_build import (
    ImageCache,
    RateLimiter,
    embed_one,
    preprocess,
    products_to_fetch,
    tcgplayer_image_urls,
    write_catalog_npz,
)


def _png(size=(300, 300), color=(123, 45, 67)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="JPEG")
    return buf.getvalue()


# --- tcgplayer_image_urls --------------------------------------------------


def test_urls_high_res_first():
    urls = tcgplayer_image_urls(
        218276, "https://tcgplayer-cdn.tcgplayer.com/product/218276_200w.jpg"
    )
    assert urls[0].endswith("218276_in_1000x1000.jpg")
    assert urls[1].endswith("218276_200w.jpg")


def test_urls_no_fallback_when_parquet_url_missing():
    urls = tcgplayer_image_urls(218276, None)
    assert len(urls) == 1
    assert urls[0].endswith("218276_in_1000x1000.jpg")


def test_urls_dedupe_when_parquet_matches_high_res():
    # If the parquet happens to already be the high-res variant, don't duplicate it.
    high_res = "https://tcgplayer-cdn.tcgplayer.com/product/218276_in_1000x1000.jpg"
    urls = tcgplayer_image_urls(218276, high_res)
    assert urls == [high_res]


# --- products_to_fetch -----------------------------------------------------


def test_products_to_fetch_filters_sealed_and_missing_urls(tmp_path):
    p = tmp_path / "products.parquet"
    pd.DataFrame(
        {
            "product_id": [1001, 1002, 9999, 1003],
            "name": ["A", "B", "Sealed", "NoURL"],
            "image_url": [
                "https://tcgplayer-cdn.tcgplayer.com/product/1001_200w.jpg",
                "https://tcgplayer-cdn.tcgplayer.com/product/1002_200w.jpg",
                "https://tcgplayer-cdn.tcgplayer.com/product/9999_200w.jpg",
                None,
            ],
            "is_sealed": [False, False, True, False],
        }
    ).to_parquet(p)

    out = list(products_to_fetch(p))
    pids = [pid for pid, _ in out]
    assert pids == [1001, 1002]  # sealed and null-URL both excluded


# --- RateLimiter -----------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_limiter_paces_acquires():
    import time as _time

    limiter = RateLimiter(rate=20.0)  # 20 req/s -> min interval 50ms

    t0 = _time.monotonic()
    for _ in range(4):
        await limiter.acquire()
    elapsed = _time.monotonic() - t0
    # 4 acquires at 20/s: first is instant, then 3 × 50ms gap = ~150ms total
    assert elapsed >= 0.14  # some tolerance for OS jitter
    assert elapsed < 0.5  # but not absurdly slow


# --- ImageCache ------------------------------------------------------------


def test_image_cache_roundtrip(tmp_path):
    cache = ImageCache(tmp_path / "imgs")
    assert not cache.has(1001)
    assert cache.get(1001) is None

    cache.put(1001, b"hello")
    assert cache.has(1001)
    assert cache.get(1001) == b"hello"
    assert cache.path(1001).name == "1001.jpg"


def test_image_cache_creates_directory(tmp_path):
    cache_dir = tmp_path / "nested" / "imgs"
    cache = ImageCache(cache_dir)
    assert cache_dir.exists()
    assert cache_dir.is_dir()


# --- preprocess ------------------------------------------------------------


def test_preprocess_returns_448x448_rgb():
    img = preprocess(_png(size=(300, 420)))
    assert img.size == (448, 448)
    assert img.mode == "RGB"


def test_preprocess_letterboxes_non_square_with_white():
    # Tall image: 100×400 → scaled to ~112×448 inside 448×448 canvas.
    # Pixels at the left edge of the canvas should be the white background.
    img = preprocess(_png(size=(100, 400), color=(0, 0, 0)))
    px = img.getpixel((0, 224))  # left edge, middle row
    assert px == (255, 255, 255)


def test_preprocess_raises_on_garbage_bytes():
    with pytest.raises(Exception):  # noqa: B017 - PIL.UnidentifiedImageError
        preprocess(b"definitely not an image")


# --- embed_one -------------------------------------------------------------


def test_embed_one_returns_unit_vector():
    from collector_vision import NeuralEmbedder

    embedder = NeuralEmbedder()
    img = preprocess(_png(color=(200, 50, 50)))
    emb = embed_one(embedder, img)
    assert emb.shape == (128,)
    assert emb.dtype == np.float32
    assert abs(float(np.linalg.norm(emb)) - 1.0) < 1e-5


# --- write_catalog_npz -----------------------------------------------------


def test_write_catalog_npz_roundtrip(tmp_path):
    import json

    embeddings = np.random.default_rng(0).standard_normal((3, 128)).astype(np.float32)
    embeddings /= np.linalg.norm(embeddings, axis=1, keepdims=True)
    ids = ["1001", "1002", "1003"]
    out = tmp_path / "catalog.npz"

    write_catalog_npz(out, ids, embeddings)

    data = np.load(out, allow_pickle=False)
    assert data["embeddings"].shape == (3, 128)
    assert data["card_ids"].tolist() == ids
    assert str(data["source"]) == "tcgplayer"
    assert json.loads(str(data["embedder_spec"]))["algo_key"] == "milo1"


def test_write_catalog_npz_accepts_alternate_source_and_algo(tmp_path):
    import json

    out = tmp_path / "c.npz"
    write_catalog_npz(
        out, ["1"], np.zeros((1, 128), dtype=np.float32), source="custom", algo_key="milo2"
    )
    data = np.load(out, allow_pickle=False)
    assert str(data["source"]) == "custom"
    assert json.loads(str(data["embedder_spec"]))["algo_key"] == "milo2"
