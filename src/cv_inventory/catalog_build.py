"""Build a TCGplayer-keyed embedding catalog from products.parquet."""

from __future__ import annotations

import io
import json
import logging
from pathlib import Path

import httpx
import numpy as np
import pandas as pd
from collector_vision import NeuralEmbedder
from PIL import Image

log = logging.getLogger(__name__)


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


def _download(client: httpx.Client, urls: list[str], dest: Path) -> bytes | None:
    for url in urls:
        try:
            r = client.get(url, timeout=15.0)
            if r.status_code == 200 and r.headers.get("content-type", "").startswith("image/"):
                dest.write_bytes(r.content)
                return r.content
        except httpx.HTTPError:
            continue
    return None


def build_catalog(products_parquet: Path, out_path: Path, image_cache: Path) -> None:
    image_cache.mkdir(parents=True, exist_ok=True)
    df = pd.read_parquet(products_parquet)
    df = df[df["is_sealed"] == False].copy()  # noqa: E712
    df = df[df["image_url"].notna()]

    embedder = NeuralEmbedder()
    embeddings = []
    ids: list[str] = []

    with httpx.Client() as client:
        for _, row in df.iterrows():
            pid = int(row["product_id"])
            cached = image_cache / f"{pid}.jpg"
            if cached.exists():
                content = cached.read_bytes()
            else:
                content = _download(
                    client,
                    [_high_res_url(pid), row["image_url"]],
                    cached,
                )
                if content is None:
                    log.warning("No image available for product %s — skipping", pid)
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

    out_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        out_path,
        embeddings=np.stack(embeddings, axis=0),
        card_ids=np.array(ids, dtype="<U36"),
        source="tcgplayer",
        embedder_spec=json.dumps({"kind": "neural", "algo_key": "milo1"}),
    )
    log.info("Wrote %d embeddings to %s", len(ids), out_path)
