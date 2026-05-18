"""Fetch images from URLs into PIL Images."""

from __future__ import annotations

import io

import httpx
from PIL import Image, UnidentifiedImageError


class FetchError(RuntimeError):
    pass


async def fetch_image(url: str, timeout: float = 10.0) -> Image.Image:
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(url)
    if resp.status_code != 200:
        raise FetchError(f"GET {url} returned {resp.status_code}")
    try:
        img = Image.open(io.BytesIO(resp.content))
        img.load()
    except UnidentifiedImageError as e:
        raise FetchError(f"Response from {url} is not a valid image") from e
    return img.convert("RGB")
