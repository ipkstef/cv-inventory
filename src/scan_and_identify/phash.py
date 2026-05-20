"""Perceptual-hash rerank: name-region cropping + 64-bit DCT pHash.

Pure functions with no engine dependency. The name-region crop ratios mirror
the layout used by the predecessor system on a 363×504 canonical card; inputs
of any size are resized to canonical before cropping so the ratios stay
meaningful regardless of upstream warp dimensions.
"""

from __future__ import annotations

import imagehash
import numpy as np
from PIL import Image

CANONICAL_WIDTH = 363
CANONICAL_HEIGHT = 504

NAME_REGION_TOP_RATIO = 0.15
NAME_REGION_LEFT_RATIO = 0.10
NAME_REGION_RIGHT_RATIO = 0.25
NAME_REGION_TOP_BORDER_PX = 5


def crop_name_region(image: Image.Image) -> Image.Image:
    """Resize `image` to canonical 363×504 RGB and crop the name-region strip.

    The result is the rectangle from (left_margin, 5) to
    (width-right_margin, top_ratio*height) — the title bar, minus the outer
    5-px border and the right-side mana-cost column.
    """
    canonical = image.convert("RGB").resize((CANONICAL_WIDTH, CANONICAL_HEIGHT), Image.LANCZOS)
    left = int(CANONICAL_WIDTH * NAME_REGION_LEFT_RATIO)
    right = CANONICAL_WIDTH - int(CANONICAL_WIDTH * NAME_REGION_RIGHT_RATIO)
    top = NAME_REGION_TOP_BORDER_PX
    bottom = int(CANONICAL_HEIGHT * NAME_REGION_TOP_RATIO)
    return canonical.crop((left, top, right, bottom))


def compute_name_phash(image: Image.Image) -> np.uint64:
    """Crop name region and return a 64-bit DCT perceptual hash as uint64."""
    region = crop_name_region(image)
    h = imagehash.phash(region, hash_size=8)
    bits = h.hash.flatten()
    value = 0
    for b in bits:
        value = (value << 1) | (1 if b else 0)
    return np.uint64(value)


def hamming_distance(a: np.uint64, b: np.uint64) -> int:
    """Number of differing bits between two 64-bit pHashes (XOR popcount)."""
    return int(bin(int(a) ^ int(b)).count("1"))
