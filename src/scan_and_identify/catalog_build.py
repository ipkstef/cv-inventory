"""Build a TCGplayer-keyed embedding catalog from products.parquet.

Parallel downloads with a global rate limiter, serial embed.  Already-cached
images are skipped, so the build is resumable: kill the process and restart
with the same --image-cache path to continue.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import time
from pathlib import Path

import httpx
import numpy as np
import pandas as pd
from collector_vision import NeuralEmbedder
from PIL import Image

log = logging.getLogger(__name__)

USER_AGENT = "scan-and-identify/0.1 (catalog-build; +https://github.com/ipkstef/scan-and-identify)"


def _high_res_url(product_id: int) -> str:
    return f"https://tcgplayer-cdn.tcgplayer.com/product/{product_id}_in_1000x1000.jpg"


def _resize_letterbox(img: Image.Image, size: int = 448) -> Image.Image:
    img = img.convert("RGB")
    w, h = img.size
    scale = size / max(w, h)
    new_w, new_h = int(w * scale), int(h * scale)
    resized = img.resize((new_w, new_h), Image.LANCZOS)
    canvas = Image.new("RGB", (size, size), (255, 255, 255))
    canvas.paste(resized, ((size - new_w) // 2, (size - new_h) // 2))
    return canvas


class _RateLimiter:
    """Token-bucket-ish: acquire() returns at most once every 1/rate seconds across all callers."""

    def __init__(self, rate: float) -> None:
        self._min_interval = 1.0 / rate
        self._next_allowed = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait = self._next_allowed - now
            if wait > 0:
                await asyncio.sleep(wait)
            self._next_allowed = max(self._next_allowed, now) + self._min_interval


async def _download_one(
    client: httpx.AsyncClient,
    limiter: _RateLimiter,
    pid: int,
    fallback_url: str,
    dest: Path,
) -> bytes | None:
    for url in (_high_res_url(pid), fallback_url):
        await limiter.acquire()
        try:
            r = await client.get(url, timeout=20.0)
        except httpx.HTTPError as e:
            log.debug("download error for %s: %s", url, e)
            continue
        if r.status_code == 200 and r.headers.get("content-type", "").startswith("image/"):
            dest.write_bytes(r.content)
            return r.content
        if r.status_code in (429, 503):
            # Be polite: pause the whole pipeline briefly on backpressure.
            log.warning("Backpressure %s on %s — sleeping 5s", r.status_code, url)
            await asyncio.sleep(5)
    return None


async def _download_batch(
    pending: list[tuple[int, str, Path]],
    rate: float,
    concurrency: int,
) -> dict[int, bytes]:
    """Download all (pid, url, dest) in pending; return {pid: bytes} for successes."""
    limiter = _RateLimiter(rate)
    sem = asyncio.Semaphore(concurrency)
    results: dict[int, bytes] = {}

    headers = {"User-Agent": USER_AGENT, "Accept": "image/*"}
    async with httpx.AsyncClient(headers=headers, http2=False) as client:

        async def worker(pid: int, url: str, dest: Path) -> None:
            async with sem:
                content = await _download_one(client, limiter, pid, url, dest)
                if content is not None:
                    results[pid] = content

        await asyncio.gather(*(worker(pid, url, dest) for pid, url, dest in pending))

    return results


def download_only(
    products_parquet: Path,
    image_cache: Path,
    *,
    rate: float = 10.0,
    concurrency: int = 16,
    batch_size: int = 1024,
) -> None:
    """Download all non-sealed product images into the cache. No embedding, no NPZ.

    Use this first to populate the cache at high rate (download is the slow part),
    then run :func:`build_catalog` to embed from the cache without network.
    """
    image_cache.mkdir(parents=True, exist_ok=True)
    df = pd.read_parquet(products_parquet)
    df = df[df["is_sealed"] == False].copy()  # noqa: E712
    df = df[df["image_url"].notna()]
    rows = list(df.itertuples(index=False))
    total = len(rows)

    log.info(
        "Downloading images for %d products (rate=%.1f req/s, concurrency=%d, batch=%d)",
        total,
        rate,
        concurrency,
        batch_size,
    )

    start = time.monotonic()
    cached_count = 0
    fetched_count = 0
    for batch_start in range(0, total, batch_size):
        batch = rows[batch_start : batch_start + batch_size]
        pending: list[tuple[int, str, Path]] = []
        for row in batch:
            pid = int(row.product_id)
            dest = image_cache / f"{pid}.jpg"
            if dest.exists():
                cached_count += 1
            else:
                pending.append((pid, row.image_url, dest))

        if pending:
            fetched = asyncio.run(_download_batch(pending, rate=rate, concurrency=concurrency))
            fetched_count += len(fetched)

        done = batch_start + len(batch)
        elapsed = time.monotonic() - start
        rate_actual = done / elapsed if elapsed > 0 else 0
        eta_sec = (total - done) / rate_actual if rate_actual > 0 else 0
        log.info(
            "Progress %d/%d (%.1f%%) — cached %d fetched %d — %.2f products/s — ETA %.1f min",
            done,
            total,
            100 * done / total,
            cached_count,
            fetched_count,
            rate_actual,
            eta_sec / 60,
        )

    log.info(
        "Done. Cached at start: %d, newly fetched: %d, total images in cache: %d",
        cached_count,
        fetched_count,
        cached_count + fetched_count,
    )


def build_catalog(
    products_parquet: Path,
    out_path: Path,
    image_cache: Path,
    *,
    rate: float = 3.0,
    concurrency: int = 4,
    batch_size: int = 256,
) -> None:
    """Build a TCGplayer-keyed embedding catalog.

    Network politeness: at most ``rate`` requests/sec across all workers,
    cap of ``concurrency`` in-flight requests.  Default 3 req/s with 4
    workers is sustainable on Cloudflare-fronted CDNs without tripping
    abuse detection.
    """
    image_cache.mkdir(parents=True, exist_ok=True)
    df = pd.read_parquet(products_parquet)
    df = df[df["is_sealed"] == False].copy()  # noqa: E712
    df = df[df["image_url"].notna()]

    embedder = NeuralEmbedder()
    embeddings: list[np.ndarray] = []
    ids: list[str] = []

    rows = list(df.itertuples(index=False))
    total = len(rows)
    log.info(
        "Building catalog from %d products (rate=%.1f req/s, concurrency=%d, batch=%d)",
        total,
        rate,
        concurrency,
        batch_size,
    )

    start = time.monotonic()
    for batch_start in range(0, total, batch_size):
        batch = rows[batch_start : batch_start + batch_size]

        pending: list[tuple[int, str, Path]] = []
        cached_now: dict[int, bytes] = {}
        for row in batch:
            pid = int(row.product_id)
            dest = image_cache / f"{pid}.jpg"
            if dest.exists():
                cached_now[pid] = dest.read_bytes()
            else:
                pending.append((pid, row.image_url, dest))

        fetched = asyncio.run(_download_batch(pending, rate=rate, concurrency=concurrency))
        cached_now.update(fetched)

        for row in batch:
            pid = int(row.product_id)
            content = cached_now.get(pid)
            if content is None:
                continue
            try:
                img = _resize_letterbox(Image.open(io.BytesIO(content)))
            except Exception as e:
                log.warning("Bad image for product %s: %s", pid, e)
                continue
            emb = np.asarray(embedder.embed(img), dtype=np.float32)
            emb = emb / np.linalg.norm(emb)
            embeddings.append(emb)
            ids.append(str(pid))

        done = batch_start + len(batch)
        elapsed = time.monotonic() - start
        rate_actual = done / elapsed if elapsed > 0 else 0
        eta_sec = (total - done) / rate_actual if rate_actual > 0 else 0
        log.info(
            "Progress %d/%d (%.1f%%) — %.2f products/s — ETA %.1f min",
            done,
            total,
            100 * done / total,
            rate_actual,
            eta_sec / 60,
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        out_path,
        embeddings=np.stack(embeddings, axis=0),
        card_ids=np.array(ids, dtype="<U36"),
        source="tcgplayer",
        embedder_spec=json.dumps({"kind": "neural", "algo_key": "milo1"}),
    )
    log.info("Wrote %d embeddings to %s", len(ids), out_path)
