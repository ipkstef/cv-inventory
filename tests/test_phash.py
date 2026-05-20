"""Unit tests for name-region cropping and pHash primitives."""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw

from scan_and_identify.phash import (
    CANONICAL_HEIGHT,
    CANONICAL_WIDTH,
    NAME_REGION_LEFT_RATIO,
    NAME_REGION_RIGHT_RATIO,
    NAME_REGION_TOP_BORDER_PX,
    NAME_REGION_TOP_RATIO,
    compute_name_phash,
    crop_name_region,
    hamming_distance,
)


def _solid(size, color):
    return Image.new("RGB", size, color)


def _expected_crop_size():
    expected_w = (
        CANONICAL_WIDTH
        - int(CANONICAL_WIDTH * NAME_REGION_LEFT_RATIO)
        - int(CANONICAL_WIDTH * NAME_REGION_RIGHT_RATIO)
    )
    expected_h = int(CANONICAL_HEIGHT * NAME_REGION_TOP_RATIO) - NAME_REGION_TOP_BORDER_PX
    return expected_w, expected_h


def test_crop_name_region_returns_expected_box():
    img = _solid((CANONICAL_WIDTH, CANONICAL_HEIGHT), (10, 20, 30))
    cropped = crop_name_region(img)
    assert cropped.size == _expected_crop_size()


def test_crop_name_region_resizes_non_canonical_inputs():
    img = _solid((720, 1008), (10, 20, 30))
    cropped = crop_name_region(img)
    assert cropped.size == _expected_crop_size()


def test_compute_name_phash_returns_uint64():
    img = _solid((CANONICAL_WIDTH, CANONICAL_HEIGHT), (10, 20, 30))
    h = compute_name_phash(img)
    assert isinstance(h, np.uint64)


def test_compute_name_phash_is_deterministic():
    img = _solid((CANONICAL_WIDTH, CANONICAL_HEIGHT), (10, 20, 30))
    assert compute_name_phash(img) == compute_name_phash(img)


def test_compute_name_phash_differs_for_different_images():
    a = Image.new("RGB", (CANONICAL_WIDTH, CANONICAL_HEIGHT), (10, 20, 30))
    b = Image.new("RGB", (CANONICAL_WIDTH, CANONICAL_HEIGHT), (200, 50, 50))
    # Paint distinguishable patterns in the name-region area: DCT of a constant
    # is trivial, so solid colors hash identically.
    ImageDraw.Draw(a).rectangle((40, 10, 100, 50), fill=(255, 255, 255))
    ImageDraw.Draw(b).rectangle((150, 10, 250, 60), fill=(0, 0, 0))
    assert compute_name_phash(a) != compute_name_phash(b)


def test_hamming_distance_zero_for_same_value():
    assert hamming_distance(np.uint64(0xDEADBEEF), np.uint64(0xDEADBEEF)) == 0


def test_hamming_distance_counts_differing_bits():
    # 0xFF ^ 0x0F = 0xF0 = 0b11110000 → 4 set bits
    assert hamming_distance(np.uint64(0xFF), np.uint64(0x0F)) == 4


def test_hamming_distance_full_64_bits():
    assert hamming_distance(np.uint64(0), np.uint64(0xFFFFFFFFFFFFFFFF)) == 64
