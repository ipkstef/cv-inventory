"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.fixtures.synthetic import write_synthetic_catalog, write_synthetic_parquets


@pytest.fixture
def synthetic_parquets(tmp_path: Path) -> Path:
    out = tmp_path / "tcg" / "1"
    write_synthetic_parquets(out)
    return out


@pytest.fixture
def synthetic_catalog(tmp_path: Path) -> Path:
    out = tmp_path / "catalog.npz"
    write_synthetic_catalog(out, product_ids=[1001, 1002, 1003, 2001])
    return out
