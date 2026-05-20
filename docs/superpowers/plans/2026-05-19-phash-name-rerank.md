# pHash-on-Name Rerank Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a perceptual-hash rerank step on top of Milo's top-K candidates to fix "right card, wrong frame" misses on retro frames and alt-art printings, without modifying the existing Milo path.

**Architecture:** At catalog-build time, compute a 64-bit DCT perceptual hash on the name region (top 15% of the card, with horizontal margins) of every product image; store as a parallel `uint64` array in the catalog NPZ. At identify time, after Milo picks the winning orientation and returns top-K candidates, compute the same name-region pHash on the input (rotated to match Milo's chosen orientation), and rerank candidates by `0.85·cosine + 0.15·(1 − hamming/64)`. Hard-fail at boot if the loaded catalog wasn't built with the new `algo_key`.

**Tech Stack:** Python 3.11+, `imagehash` (new runtime dep, ~5 KB pure Python), Pillow (already present), numpy (already present), CollectorVision engine fork (unchanged).

---

## File Structure

**New files:**
- `src/scan_and_identify/phash.py` — pure functions: `crop_name_region`, `compute_name_phash`, `hamming_distance`. No engine dependency.
- `tests/test_phash.py` — primitive unit tests.

**Modified files:**
- `pyproject.toml` — add `imagehash>=4.3` to runtime deps.
- `src/scan_and_identify/catalog_build.py` — compute pHashes during `build_catalog`; `write_catalog_npz` writes the new `name_phashes` field; new `read_catalog_phashes` loader.
- `src/scan_and_identify/set_index.py` — carry parallel pHash array; expose `name_phash_for(product_id)`.
- `src/scan_and_identify/pipeline.py` — rerank step using SetIndex pHash lookup; orientation follows Milo's pick.
- `src/scan_and_identify/server/state.py` — load pHashes; assert `algo_key == "milo1+phash1"`; pass through to `SetIndex.build`.
- `tests/fixtures/synthetic.py` — synthetic catalog includes synthetic pHashes.
- `tests/test_pipeline.py` — rerank coverage.
- `tests/test_build_primitives.py` — NPZ pHash roundtrip coverage.
- `tests/test_set_index.py` — `name_phash_for` coverage.
- `docs/ARCHITECTURE.md`, `docs/OPERATIONS.md`, `docs/API.md` — document the rerank step and catalog version bump.

**Files explicitly NOT touched:**
- The Milo embedding path (`embed_one`, `preprocess`, `IdentifyPipeline._embedder`). Per design constraint A, all Milo behaviour stays bit-for-bit identical.
- The `BackRejector` (operates on Milo embeddings only).
- The CollectorVision engine fork.

---

## Task 1: pHash primitives

**Files:**
- Modify: `pyproject.toml`
- Create: `src/scan_and_identify/phash.py`
- Create: `tests/test_phash.py`

- [ ] **Step 1: Add `imagehash` to dependencies**

Edit `pyproject.toml`, append to the `dependencies` list (after `"python-multipart>=0.0.9",`):

```toml
  "imagehash>=4.3",
```

- [ ] **Step 2: Install the new dep**

Run: `cd /Users/stefanosamanuel/scan-and-identify && uv pip install -e ".[dev]"`
Expected: imagehash installed; no other changes.

- [ ] **Step 3: Write failing test for `crop_name_region`**

Create `tests/test_phash.py`:

```python
"""Unit tests for name-region cropping and pHash primitives."""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from scan_and_identify.phash import (
    CANONICAL_HEIGHT,
    CANONICAL_WIDTH,
    NAME_REGION_LEFT_RATIO,
    NAME_REGION_RIGHT_RATIO,
    NAME_REGION_TOP_RATIO,
    compute_name_phash,
    crop_name_region,
    hamming_distance,
)


def _solid(size, color):
    return Image.new("RGB", size, color)


def test_crop_name_region_returns_expected_box():
    img = _solid((CANONICAL_WIDTH, CANONICAL_HEIGHT), (10, 20, 30))
    cropped = crop_name_region(img)
    expected_w = CANONICAL_WIDTH - int(CANONICAL_WIDTH * NAME_REGION_LEFT_RATIO) - int(
        CANONICAL_WIDTH * NAME_REGION_RIGHT_RATIO
    )
    expected_h = int(CANONICAL_HEIGHT * NAME_REGION_TOP_RATIO) - 5  # skip 5px border
    assert cropped.size == (expected_w, expected_h)


def test_crop_name_region_resizes_non_canonical_inputs():
    # Input larger than canonical should be resized first, then cropped.
    img = _solid((720, 1008), (10, 20, 30))
    cropped = crop_name_region(img)
    # Same cropped dimensions as canonical case
    expected_w = CANONICAL_WIDTH - int(CANONICAL_WIDTH * NAME_REGION_LEFT_RATIO) - int(
        CANONICAL_WIDTH * NAME_REGION_RIGHT_RATIO
    )
    expected_h = int(CANONICAL_HEIGHT * NAME_REGION_TOP_RATIO) - 5
    assert cropped.size == (expected_w, expected_h)
```

- [ ] **Step 4: Run test to verify it fails**

Run: `pytest tests/test_phash.py::test_crop_name_region_returns_expected_box -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scan_and_identify.phash'`.

- [ ] **Step 5: Implement `phash.py` minimal to pass cropping tests**

Create `src/scan_and_identify/phash.py`:

```python
"""Perceptual-hash rerank: name-region cropping + 64-bit DCT pHash.

These are pure functions with no engine dependency. The name-region
crop ratios mirror the layout used by the predecessor system on a
363×504 canonical card; inputs of any size are resized to canonical
before cropping so the ratios stay meaningful.
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

    The result is the rectangle from (left_margin, 5) to (width-right_margin,
    top_ratio*height) — i.e. the title bar, minus the outer 5 px border and
    the right-side mana-cost column.
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
    h = imagehash.phash(region, hash_size=8)  # 8×8 = 64 bits
    bits = h.hash.flatten()
    value = 0
    for b in bits:
        value = (value << 1) | (1 if b else 0)
    return np.uint64(value)


def hamming_distance(a: np.uint64, b: np.uint64) -> int:
    """Number of differing bits between two 64-bit pHashes (XOR popcount)."""
    return int(bin(int(a) ^ int(b)).count("1"))
```

- [ ] **Step 6: Run cropping test to verify pass**

Run: `pytest tests/test_phash.py::test_crop_name_region_returns_expected_box tests/test_phash.py::test_crop_name_region_resizes_non_canonical_inputs -v`
Expected: 2 PASS.

- [ ] **Step 7: Add failing tests for pHash compute and Hamming distance**

Append to `tests/test_phash.py`:

```python
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
    # Paint distinguishable patterns in the name-region area so the DCT actually
    # captures structure (solid colors hash identically because DCT of a constant
    # is zero except DC).
    import PIL.ImageDraw as D

    D.Draw(a).rectangle((40, 10, 100, 50), fill=(255, 255, 255))
    D.Draw(b).rectangle((150, 10, 250, 60), fill=(0, 0, 0))
    assert compute_name_phash(a) != compute_name_phash(b)


def test_hamming_distance_zero_for_same_value():
    assert hamming_distance(np.uint64(0xDEADBEEF), np.uint64(0xDEADBEEF)) == 0


def test_hamming_distance_counts_differing_bits():
    # 0xFF ^ 0x0F = 0xF0 = 0b11110000 → 4 set bits
    assert hamming_distance(np.uint64(0xFF), np.uint64(0x0F)) == 4


def test_hamming_distance_full_64_bits():
    assert hamming_distance(np.uint64(0), np.uint64(0xFFFFFFFFFFFFFFFF)) == 64
```

- [ ] **Step 8: Run new tests**

Run: `pytest tests/test_phash.py -v`
Expected: 7 PASS.

- [ ] **Step 9: Commit**

```bash
cd /Users/stefanosamanuel/scan-and-identify
git add pyproject.toml src/scan_and_identify/phash.py tests/test_phash.py
git commit -m "Add pHash primitives for name-region rerank"
```

---

## Task 2: Catalog build writes name_phashes

**Files:**
- Modify: `src/scan_and_identify/catalog_build.py`
- Modify: `tests/test_build_primitives.py`

- [ ] **Step 1: Write failing tests for new NPZ fields**

Append to `tests/test_build_primitives.py`:

```python
def test_write_catalog_npz_writes_name_phashes(tmp_path):
    out = tmp_path / "catalog.npz"
    embeddings = np.random.default_rng(0).standard_normal((2, 128)).astype(np.float32)
    embeddings /= np.linalg.norm(embeddings, axis=1, keepdims=True)
    phashes = np.array([0xDEADBEEF, 0xCAFEF00D], dtype=np.uint64)
    write_catalog_npz(out, ["1", "2"], embeddings, name_phashes=phashes)
    data = np.load(out, allow_pickle=False)
    assert data["name_phashes"].tolist() == [0xDEADBEEF, 0xCAFEF00D]
    assert data["name_phashes"].dtype == np.uint64
    import json
    assert json.loads(str(data["embedder_spec"]))["algo_key"] == "milo1+phash1"


def test_write_catalog_npz_without_phashes_keeps_old_algo_key(tmp_path):
    # Back-compat: callers that don't pass phashes get the legacy algo_key.
    out = tmp_path / "catalog.npz"
    embeddings = np.random.default_rng(0).standard_normal((1, 128)).astype(np.float32)
    embeddings /= np.linalg.norm(embeddings, axis=1, keepdims=True)
    write_catalog_npz(out, ["1"], embeddings)
    import json
    data = np.load(out, allow_pickle=False)
    assert "name_phashes" not in data.files
    assert json.loads(str(data["embedder_spec"]))["algo_key"] == "milo1"


def test_read_catalog_name_phashes_returns_none_when_absent(tmp_path):
    from scan_and_identify.catalog_build import read_catalog_name_phashes

    out = tmp_path / "catalog.npz"
    embeddings = np.zeros((1, 128), dtype=np.float32)
    write_catalog_npz(out, ["1"], embeddings)
    assert read_catalog_name_phashes(out) is None


def test_read_catalog_name_phashes_roundtrip(tmp_path):
    from scan_and_identify.catalog_build import read_catalog_name_phashes

    out = tmp_path / "catalog.npz"
    embeddings = np.zeros((2, 128), dtype=np.float32)
    phashes = np.array([0xAAAA, 0xBBBB], dtype=np.uint64)
    write_catalog_npz(out, ["1", "2"], embeddings, name_phashes=phashes)
    loaded = read_catalog_name_phashes(out)
    assert loaded is not None
    assert loaded.tolist() == [0xAAAA, 0xBBBB]
    assert loaded.dtype == np.uint64
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_build_primitives.py::test_write_catalog_npz_writes_name_phashes tests/test_build_primitives.py::test_read_catalog_name_phashes_roundtrip -v`
Expected: FAIL — `write_catalog_npz` doesn't accept `name_phashes`; `read_catalog_name_phashes` doesn't exist.

- [ ] **Step 3: Update `write_catalog_npz` signature and behaviour**

In `src/scan_and_identify/catalog_build.py`, replace the `write_catalog_npz` function:

```python
def write_catalog_npz(
    path: Path,
    card_ids: list[str],
    embeddings: np.ndarray,
    *,
    source: str = "tcgplayer",
    algo_key: str | None = None,
    built_at: str | None = None,
    name_phashes: np.ndarray | None = None,
) -> None:
    """Write an NPZ in the format CollectorVision's :class:`Catalog` reads.

    ``embeddings`` should already be L2-normalised (use :func:`embed_one`).
    ``card_ids`` should be parallel to embeddings — same length, same order.
    ``built_at`` is an ISO-8601 UTC timestamp; defaults to "now".
    ``name_phashes``, if provided, is a parallel uint64 array of 64-bit
    perceptual hashes of the card name region. When phashes are present the
    default ``algo_key`` is bumped to ``"milo1+phash1"`` to fence off catalogs
    that lack the new field — pass an explicit ``algo_key`` to override.
    """
    from datetime import datetime

    path.parent.mkdir(parents=True, exist_ok=True)
    if built_at is None:
        built_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    if algo_key is None:
        algo_key = "milo1+phash1" if name_phashes is not None else "milo1"

    arrays = {
        "embeddings": embeddings,
        "card_ids": np.array(card_ids, dtype="<U36"),
        "source": np.asarray(source),
        "embedder_spec": np.asarray(json.dumps({"kind": "neural", "algo_key": algo_key})),
        "built_at": np.asarray(built_at),
    }
    if name_phashes is not None:
        if name_phashes.dtype != np.uint64:
            name_phashes = name_phashes.astype(np.uint64)
        if name_phashes.shape != (len(card_ids),):
            raise ValueError(
                f"name_phashes length {name_phashes.shape} doesn't match card_ids ({len(card_ids)})"
            )
        arrays["name_phashes"] = name_phashes
    np.savez_compressed(path, **arrays)
```

- [ ] **Step 4: Add `read_catalog_name_phashes`**

Append to `src/scan_and_identify/catalog_build.py` right after `read_catalog_built_at`:

```python
def read_catalog_name_phashes(path: Path) -> np.ndarray | None:
    """Return the ``name_phashes`` uint64 array, or None if the NPZ lacks it.

    Old NPZs from before pHash rerank was added return None — callers should
    treat that as "fall back to embedding-only" and/or hard-fail at boot
    depending on policy.
    """
    data = np.load(path, allow_pickle=False)
    if "name_phashes" not in data.files:
        return None
    arr = data["name_phashes"]
    return arr.astype(np.uint64) if arr.dtype != np.uint64 else arr
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_build_primitives.py -v`
Expected: all existing pass + 4 new pass.

- [ ] **Step 6: Wire pHash compute into `build_catalog` orchestrator**

In `src/scan_and_identify/catalog_build.py`, replace the Phase 3 block inside `build_catalog`:

Old (around the `# Phase 3: preprocess + embed in batch order.` block):

```python
        # Phase 3: preprocess + embed in batch order.
        for pid, _ in batch:
            content = in_memory.get(pid)
            if content is None:
                continue  # download failed for this product; skip silently
            try:
                img = preprocess(content)
            except Exception as e:
                log.warning("Bad image for product %s: %s", pid, e)
                continue
            embeddings.append(embed_one(embedder, img))
            card_ids.append(str(pid))
```

Replace with:

```python
        # Phase 3: preprocess + embed + pHash in batch order.
        for pid, _ in batch:
            content = in_memory.get(pid)
            if content is None:
                continue  # download failed for this product; skip silently
            try:
                img = preprocess(content)
                # pHash uses a separate preprocessing path (canonical resize +
                # name-region crop) — see scan_and_identify.phash. We feed it
                # the same decoded image, not Milo's letterboxed 448×448.
                raw = Image.open(io.BytesIO(content)).convert("RGB")
                phash = compute_name_phash(raw)
            except Exception as e:
                log.warning("Bad image for product %s: %s", pid, e)
                continue
            embeddings.append(embed_one(embedder, img))
            name_phashes.append(phash)
            card_ids.append(str(pid))

        _log_progress(batch_start + len(batch), total, start)
```

Also add `name_phashes: list[np.uint64] = []` next to `embeddings: list[np.ndarray] = []` near the top of `build_catalog`, and update the import block at the top of the file:

```python
from scan_and_identify.phash import compute_name_phash
```

And the final NPZ write:

Old:
```python
    write_catalog_npz(out_path, card_ids, np.stack(embeddings, axis=0))
```

New:
```python
    write_catalog_npz(
        out_path,
        card_ids,
        np.stack(embeddings, axis=0),
        name_phashes=np.array(name_phashes, dtype=np.uint64),
    )
```

- [ ] **Step 7: Add an integration test for `build_catalog` writing pHashes**

Append to `tests/test_catalog_build.py` (use the existing pattern from that file — read the file first if needed):

```python
def test_build_catalog_writes_name_phashes(tmp_path, monkeypatch):
    """End-to-end: build_catalog produces a NPZ with name_phashes and milo1+phash1 algo_key."""
    import json
    import io
    from pathlib import Path

    import numpy as np
    import pandas as pd
    from PIL import Image

    from scan_and_identify.catalog_build import build_catalog, ImageCache

    # Synthetic parquet with 2 products
    products = tmp_path / "products.parquet"
    pd.DataFrame(
        {
            "product_id": [1001, 1002],
            "name": ["A", "B"],
            "image_url": [
                "https://tcgplayer-cdn.tcgplayer.com/product/1001_200w.jpg",
                "https://tcgplayer-cdn.tcgplayer.com/product/1002_200w.jpg",
            ],
            "is_sealed": [False, False],
        }
    ).to_parquet(products)

    # Pre-seed image cache so no network is touched
    cache_dir = tmp_path / "imgs"
    cache = ImageCache(cache_dir)
    for pid, color in [(1001, (200, 50, 50)), (1002, (50, 200, 50))]:
        buf = io.BytesIO()
        Image.new("RGB", (400, 560), color).save(buf, format="JPEG")
        cache.put(pid, buf.getvalue())

    out = tmp_path / "catalog.npz"
    build_catalog(products, out, cache_dir, rate=100, concurrency=1, batch_size=2)

    data = np.load(out, allow_pickle=False)
    assert "name_phashes" in data.files
    assert data["name_phashes"].shape == (2,)
    assert data["name_phashes"].dtype == np.uint64
    assert json.loads(str(data["embedder_spec"]))["algo_key"] == "milo1+phash1"
```

- [ ] **Step 8: Run all build tests**

Run: `pytest tests/test_build_primitives.py tests/test_catalog_build.py -v`
Expected: all PASS.

- [ ] **Step 9: Commit**

```bash
cd /Users/stefanosamanuel/scan-and-identify
git add src/scan_and_identify/catalog_build.py tests/test_build_primitives.py tests/test_catalog_build.py
git commit -m "Write name_phashes to catalog NPZ during build"
```

---

## Task 3: SetIndex carries pHashes

**Files:**
- Modify: `src/scan_and_identify/set_index.py`
- Modify: `tests/test_set_index.py`

- [ ] **Step 1: Write failing test for `name_phash_for`**

Append to `tests/test_set_index.py` (read the file first to match the existing fixture pattern; minimal example below):

```python
def test_set_index_exposes_name_phash_for_product(synthetic_catalog, synthetic_parquets):
    """SetIndex.build with name_phashes lets callers look up each product's hash."""
    import numpy as np
    from collector_vision import Catalog

    from scan_and_identify.set_index import SetIndex
    from scan_and_identify.tcgplayer.store import TCGStore

    catalog = Catalog.load(synthetic_catalog)
    store = TCGStore.load(synthetic_parquets)
    phashes = {int(cid): np.uint64(0xDEAD0000 | i) for i, cid in enumerate(catalog.card_ids)}
    phash_array = np.array([phashes[int(cid)] for cid in catalog.card_ids], dtype=np.uint64)

    index = SetIndex.build(catalog, store, name_phashes=phash_array)
    for cid in catalog.card_ids:
        pid = int(cid)
        assert index.name_phash_for(pid) == phashes[pid]


def test_set_index_returns_none_when_phashes_not_provided(synthetic_catalog, synthetic_parquets):
    from collector_vision import Catalog

    from scan_and_identify.set_index import SetIndex
    from scan_and_identify.tcgplayer.store import TCGStore

    catalog = Catalog.load(synthetic_catalog)
    store = TCGStore.load(synthetic_parquets)
    index = SetIndex.build(catalog, store)
    assert index.name_phash_for(int(catalog.card_ids[0])) is None
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_set_index.py::test_set_index_exposes_name_phash_for_product -v`
Expected: FAIL — `SetIndex.build` doesn't accept `name_phashes`.

- [ ] **Step 3: Add `name_phashes` parameter and lookup to `SetIndex`**

Replace `src/scan_and_identify/set_index.py` with:

```python
"""Per-set sub-catalogs for hard set-lock filtering."""

from __future__ import annotations

import numpy as np
from collector_vision import Catalog

from scan_and_identify.tcgplayer.store import TCGStore


class SetIndex:
    """Holds the full catalog plus one Catalog-shaped sub-view per group_id present.

    Optionally carries a parallel ``{product_id -> name_phash}`` lookup so the
    identify pipeline can rerank top-K candidates by perceptual-hash similarity
    against the input image's name region.
    """

    def __init__(
        self,
        full: Catalog,
        by_set: dict[int, Catalog],
        name_phash_by_pid: dict[int, np.uint64] | None = None,
    ) -> None:
        self._full = full
        self._by_set = by_set
        self._name_phash_by_pid = name_phash_by_pid or {}

    @classmethod
    def build(
        cls,
        catalog: Catalog,
        store: TCGStore,
        name_phashes: np.ndarray | None = None,
    ) -> SetIndex:
        product_ids = [int(s) for s in catalog.card_ids]
        group_ids = np.array(
            [store.product(pid)["group_id"] if store.product(pid) else -1 for pid in product_ids],
            dtype=np.int64,
        )

        if name_phashes is not None:
            if name_phashes.shape != (len(product_ids),):
                raise ValueError(
                    f"name_phashes shape {name_phashes.shape} doesn't match "
                    f"catalog size ({len(product_ids)})"
                )
            phash_by_pid = {pid: np.uint64(name_phashes[i]) for i, pid in enumerate(product_ids)}
        else:
            phash_by_pid = None

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
        return cls(full=catalog, by_set=by_set, name_phash_by_pid=phash_by_pid)

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

    def name_phash_for(self, product_id: int) -> np.uint64 | None:
        """Return the catalog's name-region pHash for `product_id`, or None."""
        return self._name_phash_by_pid.get(int(product_id))
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_set_index.py -v`
Expected: all PASS (existing tests still pass because `name_phashes` is optional; new tests pass).

- [ ] **Step 5: Commit**

```bash
cd /Users/stefanosamanuel/scan-and-identify
git add src/scan_and_identify/set_index.py tests/test_set_index.py
git commit -m "Carry name pHashes on SetIndex for rerank lookup"
```

---

## Task 4: Pipeline reranks top-K with pHash

**Files:**
- Modify: `src/scan_and_identify/pipeline.py`
- Modify: `tests/test_pipeline.py`

- [ ] **Step 1: Write failing test that pipeline reranks by combined score**

Append to `tests/test_pipeline.py`:

```python
def test_pipeline_reranks_by_combined_score(synthetic_catalog, synthetic_parquets):
    """When two candidates are close on embedding but one is much closer on pHash,
    the pHash-similar one wins after rerank.
    """
    import numpy as np
    from collector_vision import Catalog, NeuralEmbedder

    from scan_and_identify.back_rejector import BackRejector
    from scan_and_identify.pipeline import IdentifyPipeline
    from scan_and_identify.set_index import SetIndex
    from scan_and_identify.tcgplayer.store import TCGStore

    catalog = Catalog.load(synthetic_catalog)
    store = TCGStore.load(synthetic_parquets)
    embedder = NeuralEmbedder()

    # Use a deterministic input. Compute its actual name pHash so one catalog
    # entry can be made to match perfectly, while the others stay random.
    img = Image.new("RGB", (448, 448), (200, 50, 50))
    from scan_and_identify.phash import compute_name_phash

    matching_phash = compute_name_phash(img)
    pids = [int(cid) for cid in catalog.card_ids]

    # Build a phash array where the *second-best* embedding candidate gets the
    # exact matching pHash, and the top-1 embedding gets a maximally-distant hash.
    # The rerank should bump the second-best to first.
    rng = np.random.default_rng(0)
    phashes = np.array(
        [np.uint64(rng.integers(0, 2**63, dtype=np.uint64)) for _ in pids],
        dtype=np.uint64,
    )

    # Probe embedding-only ordering to identify "top-1" and "top-2" before rerank.
    pre_index = SetIndex.build(catalog, store, name_phashes=None)
    arr = np.asarray(embedder.embed(img.convert("RGB").resize((448, 448))), dtype=np.float32)
    pre_hits = pre_index.search(arr, set_id=None, top_k=3)
    top1_pid = pre_hits[0][1]
    top2_pid = pre_hits[1][1]

    # Now assign hashes so top1 is maximally distant, top2 matches perfectly.
    idx_top1 = pids.index(top1_pid)
    idx_top2 = pids.index(top2_pid)
    phashes[idx_top1] = np.uint64(~int(matching_phash) & 0xFFFFFFFFFFFFFFFF)
    phashes[idx_top2] = matching_phash

    index = SetIndex.build(catalog, store, name_phashes=phashes)
    pipeline = IdentifyPipeline(
        embedder=embedder,
        index=index,
        store=store,
        back_rejector=BackRejector(back_embedding=None),
    )
    result = pipeline.identify(img, set_id=None, top_k=3, rotation_invariant=False)
    assert result.candidates[0].product_id == top2_pid


def test_pipeline_skips_rerank_when_phashes_missing(synthetic_catalog, synthetic_parquets):
    """If the SetIndex has no pHashes, the pipeline returns embedding-only ranking unchanged."""
    from collector_vision import Catalog, NeuralEmbedder

    from scan_and_identify.back_rejector import BackRejector
    from scan_and_identify.pipeline import IdentifyPipeline
    from scan_and_identify.set_index import SetIndex
    from scan_and_identify.tcgplayer.store import TCGStore

    catalog = Catalog.load(synthetic_catalog)
    store = TCGStore.load(synthetic_parquets)
    embedder = NeuralEmbedder()
    index = SetIndex.build(catalog, store, name_phashes=None)
    pipeline = IdentifyPipeline(
        embedder=embedder, index=index, store=store, back_rejector=BackRejector(back_embedding=None)
    )
    img = Image.new("RGB", (448, 448), (200, 50, 50))
    result = pipeline.identify(img, set_id=None, top_k=3, rotation_invariant=False)
    assert len(result.candidates) == 3
    # Embedding-only ordering: scores should be monotonically non-increasing
    scores = [c.score for c in result.candidates]
    assert scores == sorted(scores, reverse=True)
```

- [ ] **Step 2: Run new tests to verify failure**

Run: `pytest tests/test_pipeline.py::test_pipeline_reranks_by_combined_score -v`
Expected: FAIL — currently pipeline doesn't rerank.

- [ ] **Step 3: Add rerank step to `IdentifyPipeline.identify`**

Replace `src/scan_and_identify/pipeline.py` with:

```python
"""End-to-end identification: PIL image -> top-K TCGplayer candidates with metadata."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
from collector_vision import NeuralEmbedder, rotate_card_180
from PIL import Image

from scan_and_identify.back_rejector import BackRejector
from scan_and_identify.phash import compute_name_phash, hamming_distance
from scan_and_identify.set_index import SetIndex
from scan_and_identify.tcgplayer.store import TCGStore

Confidence = Literal["good", "fair", "poor"]

PHASH_RERANK_WEIGHT = 0.15  # 15% pHash, 85% embedding (matches predecessor system)


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


DEFAULT_THRESHOLDS = ConfidenceThresholds()


def classify_confidence(top_score: float, gap_to_next: float) -> Confidence:
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
    confidence: Confidence | None = None


def _rerank_with_phash(
    hits: list[tuple[float, int]],
    query_phash: np.uint64,
    index: SetIndex,
) -> list[tuple[float, int]]:
    """Reorder (score, pid) hits by 0.85*embedding + 0.15*(1 - hamming/64).

    If `index` lacks a pHash for a hit's product (or has none at all), the
    pHash term degrades to 0 for that candidate — embedding score still wins.
    """
    reranked: list[tuple[float, int]] = []
    for score, pid in hits:
        ref_phash = index.name_phash_for(pid)
        if ref_phash is None:
            phash_score = 0.0
        else:
            phash_score = 1.0 - hamming_distance(query_phash, ref_phash) / 64.0
        combined = (1.0 - PHASH_RERANK_WEIGHT) * score + PHASH_RERANK_WEIGHT * phash_score
        reranked.append((combined, pid))
    reranked.sort(key=lambda x: x[0], reverse=True)
    return reranked


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
        # Milo path: unchanged. Letterbox to 448×448, embed at 0° and (optionally) 180°.
        rgb = image.convert("RGB")
        img = rgb.resize((448, 448))

        if rotation_invariant:
            rotated = rotate_card_180(img)
            emb_a = np.asarray(self._embedder.embed(img), dtype=np.float32)
            emb_b = np.asarray(self._embedder.embed(rotated), dtype=np.float32)
            hits_a = self._index.search(emb_a, set_id=set_id, top_k=top_k)
            hits_b = self._index.search(emb_b, set_id=set_id, top_k=top_k)
            if hits_b and (not hits_a or hits_b[0][0] > hits_a[0][0]):
                emb, hits, used_180 = emb_b, hits_b, True
            else:
                emb, hits, used_180 = emb_a, hits_a, False
        else:
            emb = np.asarray(self._embedder.embed(img), dtype=np.float32)
            hits = self._index.search(emb, set_id=set_id, top_k=top_k)
            used_180 = False

        top_score = hits[0][0] if hits else 0.0
        if self._back.is_back(embedding=emb, top_catalog_score=top_score):
            return IdentifyResult(is_card_back=True, candidates=[], confidence=None)

        # pHash rerank: use the same orientation Milo picked. Skip entirely if
        # the index carries no pHashes (e.g. legacy synthetic catalogs in tests).
        if hits and self._index.name_phash_for(int(hits[0][1])) is not None:
            phash_input = rgb.transpose(Image.ROTATE_180) if used_180 else rgb
            query_phash = compute_name_phash(phash_input)
            hits = _rerank_with_phash(hits, query_phash, self._index)

        candidates: list[Candidate] = []
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
```

- [ ] **Step 4: Run pipeline tests**

Run: `pytest tests/test_pipeline.py -v`
Expected: all 5 PASS (3 existing + 2 new).

- [ ] **Step 5: Commit**

```bash
cd /Users/stefanosamanuel/scan-and-identify
git add src/scan_and_identify/pipeline.py tests/test_pipeline.py
git commit -m "Rerank Milo top-K with name-region pHash"
```

---

## Task 5: Bootstrap loads pHashes and asserts algo_key

**Files:**
- Modify: `src/scan_and_identify/server/state.py`
- Modify: `tests/fixtures/synthetic.py`
- Modify: `tests/test_server_health.py` (or wherever bootstrap is exercised — add a new file if needed)

- [ ] **Step 1: Update synthetic catalog fixture to include pHashes and bumped algo_key**

Replace `write_synthetic_catalog` in `tests/fixtures/synthetic.py`:

```python
def write_synthetic_catalog(path: Path, product_ids: list[int]) -> None:
    """Write a CollectorVision-format NPZ with random unit vectors + random pHashes."""
    rng = np.random.default_rng(42)
    n = len(product_ids)
    embeddings = rng.standard_normal((n, 128)).astype(np.float32)
    embeddings /= np.linalg.norm(embeddings, axis=1, keepdims=True)
    name_phashes = rng.integers(0, 2**63, size=n, dtype=np.uint64)
    np.savez_compressed(
        path,
        embeddings=embeddings,
        card_ids=np.array([str(pid) for pid in product_ids], dtype="<U36"),
        source="tcgplayer",
        embedder_spec=json.dumps({"kind": "neural", "algo_key": "milo1+phash1"}),
        built_at="2026-05-19T12:34:56Z",
        name_phashes=name_phashes,
    )
```

- [ ] **Step 2: Write failing test asserting bootstrap rejects old catalogs**

Create `tests/test_bootstrap_algo_check.py`:

```python
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

    with pytest.raises(ValueError, match="milo1\\+phash1"):
        AppState.bootstrap_for_tests(api_key="x", catalog_path=catalog, parquet_dir=parquet_dir)
```

- [ ] **Step 3: Run to verify failure**

Run: `pytest tests/test_bootstrap_algo_check.py -v`
Expected: FAIL — bootstrap doesn't yet check.

- [ ] **Step 4: Add algo_key check and pHash loading to `AppState`**

Replace `src/scan_and_identify/server/state.py` with:

```python
"""Singleton app state assembled once at boot."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from collector_vision import Catalog, NeuralEmbedder

from scan_and_identify.back_rejector import BackRejector
from scan_and_identify.catalog_build import read_catalog_built_at, read_catalog_name_phashes
from scan_and_identify.config import Config
from scan_and_identify.pipeline import ConfidenceThresholds, IdentifyPipeline
from scan_and_identify.set_index import SetIndex
from scan_and_identify.tcgplayer.r2_sync import sync_parquets
from scan_and_identify.tcgplayer.store import TCGStore

REQUIRED_ALGO_KEY = "milo1+phash1"


def _derive_version(built_at: str | None, fallback_stem: str) -> str:
    if built_at and len(built_at) >= 7:
        return built_at[:7]
    return fallback_stem


def _assert_algo_key_supported(catalog_path: Path) -> None:
    """Read the NPZ's embedder_spec and refuse anything other than milo1+phash1.

    We intentionally don't fall back to embedding-only on legacy catalogs:
    silent degradation is the path to confusing prod incidents. Rebuild and
    re-deploy is the answer.
    """
    data = np.load(catalog_path, allow_pickle=False)
    spec_raw = str(data["embedder_spec"])
    spec = json.loads(spec_raw)
    algo_key = spec.get("algo_key")
    if algo_key != REQUIRED_ALGO_KEY:
        raise ValueError(
            f"Catalog at {catalog_path} has algo_key={algo_key!r} but this server "
            f"requires {REQUIRED_ALGO_KEY!r}. Rebuild the catalog with the current "
            f"scan-and-identify image (scripts/refresh-catalog.sh) and redeploy."
        )


@dataclass
class AppState:
    api_key: str
    catalog: Catalog
    catalog_version: str
    catalog_built_at: str | None
    embedder: NeuralEmbedder
    store: TCGStore
    set_index: SetIndex
    pipeline: IdentifyPipeline
    parquet_synced_at: datetime

    @classmethod
    def bootstrap(cls, config: Config, parquet_cache: Path, back_image: Path | None) -> AppState:
        sync_parquets(config.r2, config.tcgplayer_category, parquet_cache)
        parquet_dir = parquet_cache / str(config.tcgplayer_category)
        store = TCGStore.load(parquet_dir)
        _assert_algo_key_supported(config.catalog_path)
        catalog = Catalog.load(config.catalog_path)
        built_at = read_catalog_built_at(config.catalog_path)
        name_phashes = read_catalog_name_phashes(config.catalog_path)
        embedder = NeuralEmbedder()
        index = SetIndex.build(catalog, store, name_phashes=name_phashes)
        back = BackRejector.load(back_image, embedder)
        pipeline = IdentifyPipeline(
            embedder=embedder,
            index=index,
            store=store,
            back_rejector=back,
            confidence_thresholds=ConfidenceThresholds.from_env(),
        )
        return cls(
            api_key=config.api_key,
            catalog=catalog,
            catalog_version=_derive_version(built_at, config.catalog_path.stem),
            catalog_built_at=built_at,
            embedder=embedder,
            store=store,
            set_index=index,
            pipeline=pipeline,
            parquet_synced_at=datetime.now(UTC),
        )

    @classmethod
    def bootstrap_for_tests(cls, api_key: str, catalog_path: Path, parquet_dir: Path) -> AppState:
        store = TCGStore.load(parquet_dir)
        _assert_algo_key_supported(catalog_path)
        catalog = Catalog.load(catalog_path)
        built_at = read_catalog_built_at(catalog_path)
        name_phashes = read_catalog_name_phashes(catalog_path)
        embedder = NeuralEmbedder()
        index = SetIndex.build(catalog, store, name_phashes=name_phashes)
        back = BackRejector.load(None, embedder)
        pipeline = IdentifyPipeline(embedder=embedder, index=index, store=store, back_rejector=back)
        return cls(
            api_key=api_key,
            catalog=catalog,
            catalog_version=_derive_version(built_at, catalog_path.stem),
            catalog_built_at=built_at,
            embedder=embedder,
            store=store,
            set_index=index,
            pipeline=pipeline,
            parquet_synced_at=datetime.now(UTC),
        )
```

- [ ] **Step 5: Run bootstrap tests**

Run: `pytest tests/test_bootstrap_algo_check.py -v`
Expected: PASS.

- [ ] **Step 6: Run the entire suite to confirm nothing else broke**

Run: `pytest`
Expected: full suite PASS. The synthetic-fixture change in Task 5 Step 1 propagated `milo1+phash1` + random pHashes to every server test, which is exactly what the new boot guard requires.

- [ ] **Step 7: Commit**

```bash
cd /Users/stefanosamanuel/scan-and-identify
git add src/scan_and_identify/server/state.py tests/fixtures/synthetic.py tests/test_bootstrap_algo_check.py
git commit -m "Hard-fail boot on legacy catalogs without name pHashes"
```

---

## Task 6: Documentation

**Files:**
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/OPERATIONS.md`
- Modify: `docs/API.md`

- [ ] **Step 1: Add rerank step to ARCHITECTURE.md**

Add a new subsection inside `docs/ARCHITECTURE.md` (read the file first; insert after the existing identify-flow description):

```markdown
### Per-request flow: pHash rerank

After Milo returns its top-K candidates (with rotation-invariant orientation
selected), the pipeline computes a 64-bit perceptual hash on the **name
region** of the input image — the top ~15% of the card with horizontal
margins, on a 363×504 canonical resize. The pHash uses the same orientation
Milo picked (input is rotated 180° if Milo's 180° embedding scored higher).

Each top-K candidate's pre-computed catalog pHash (loaded from NPZ at boot,
held in `SetIndex._name_phash_by_pid`) is XOR'd against the query pHash;
Hamming distance is converted to a similarity score in [0, 1]. Final ranking:

```
combined_score = 0.85 * cosine_similarity + 0.15 * (1 - hamming / 64)
```

This rescues "right card, wrong frame" misses — alt-art and retro-frame
printings that Milo confuses because the artwork differs, but whose name
bars look identical to the standard printing.

The Milo embedding path itself is **unchanged**: same 448×448 letterbox,
same ONNX inference, same rotation selection. The pHash compute is purely
additive (~1 ms per identify).
```

- [ ] **Step 2: Update OPERATIONS.md catalog refresh + boot failure docs**

In `docs/OPERATIONS.md`:

1. In the "Common failures and what they mean" table, add a new row before "OOM during boot":

```markdown
| Container exits with `ValueError: ... requires 'milo1+phash1'` | Loaded catalog was built before pHash rerank was added | Rebuild with `scripts/refresh-catalog.sh` and redeploy; this is intentional — no silent fallback |
```

2. In the "Catalog refresh (monthly)" section, replace the "3. Re-embeds the full catalog" bullet with:

```markdown
3. Re-embeds the full catalog **and computes name-region pHashes** (~75 min CPU; the slow step).
```

3. Add a note after the "Step 3. Deploy" block:

```markdown
### Catalog versioning

The NPZ embeds an `algo_key` field. Current servers require `milo1+phash1`
(Milo embedder + 64-bit name-region pHash). Older `milo1` catalogs are
rejected at boot with a clear error — there is no silent fallback. If you
roll back the server image to a pre-pHash version, you must also roll the
catalog back to a `milo1`-tagged NPZ.
```

- [ ] **Step 3: Note the rerank in API.md**

In `docs/API.md`, add a short note in the `/identify` section (read the file first to match the existing style; suggested wording):

```markdown
**Scoring note:** The `score` field is the combined embedding+pHash similarity
in [0, 1] (0.85·cosine + 0.15·(1 − hamming/64)). Confidence tiers
(`good`/`fair`/`poor`) are derived from this combined score and the gap to
the next candidate, so existing tier semantics are preserved.
```

- [ ] **Step 4: Commit**

```bash
cd /Users/stefanosamanuel/scan-and-identify
git add docs/ARCHITECTURE.md docs/OPERATIONS.md docs/API.md
git commit -m "Document pHash rerank step and catalog version bump"
```

---

## Task 7: Build and tag a versioned image, rebuild production catalog

**Files:**
- None to edit. This is operational.

- [ ] **Step 1: Run the full test suite one more time**

Run: `pytest`
Expected: all PASS.

- [ ] **Step 2: Push to main and trigger CI image build**

```bash
cd /Users/stefanosamanuel/scan-and-identify
git push origin main
```

Then in GitHub: Actions → **Build image** → Run workflow. Wait ~3-5 min for the new image to publish to `ghcr.io/ipkstef/scan-and-identify:latest`.

- [ ] **Step 3: Rebuild the production catalog on mtg-eye**

```bash
ssh mtg-eye
cd ~/scan-and-identify
git pull origin main
docker compose pull   # pulls the new server image (needed because refresh-catalog.sh runs INSIDE it)
IMAGE_CACHE=/home/sammy/cv-build/imgs ./scripts/refresh-catalog.sh
```

Note the explicit `IMAGE_CACHE` — the warm cache (~110k PNGs already downloaded)
lives at `/home/sammy/cv-build/imgs` on mtg-eye. Without this override the
script defaults to `~/scan-and-identify-cache/` and will re-download every image
from the TCGplayer CDN, adding hours to the rebuild.

Expected: ~75 min embed phase + a few seconds pHash compute → `catalogs/catalog.npz` updated with `algo_key=milo1+phash1`.

- [ ] **Step 4: Commit and push the new catalog**

```bash
cd ~/scan-and-identify
git add catalogs/catalog.npz
git commit -m "Refresh catalog with name pHashes (milo1+phash1)"
git push origin main
```

- [ ] **Step 5: Rebuild the image (catalog is baked in) and redeploy**

In GitHub Actions: Build image → Run workflow. After it completes:

```bash
ssh mtg-eye
cd ~/scan-and-identify
docker compose pull && docker compose up -d
```

- [ ] **Step 6: Verify**

```bash
curl -fsS -H "Authorization: Bearer $KEY" http://65.108.128.94:8000/health | jq
```

Expected: `catalog_version` shows current `YYYY-MM`, `catalog_built_at` is today's UTC ISO timestamp. Container started without ValueError.

If `/health` 500s or container exits, check logs with `docker logs scan-and-identify` — the most likely failure mode is the algo_key guard tripping because we deployed the new image before the new catalog was baked in. Rebuild the image with the new NPZ committed and redeploy.

---

## Task 8: Re-run eval and recalibrate confidence thresholds

**Files:**
- Possibly modify: `.env` on mtg-eye (`SCAN_AND_IDENTIFY_CONF_*` overrides) — not committed to repo.

- [ ] **Step 1: Re-run the 172-scan eval against production**

From your laptop:

```bash
cd /Users/stefanosamanuel/scan-and-identify
python3 scripts/eval_review.py --scans scan_images/ --out report_phash.html \
  --api-base http://65.108.128.94:8000 --api-key "$KEY" --top-k 5
```

Open `report_phash.html` in a browser. Compare to the pre-pHash baseline
(`report.html` from the prior run, if you kept it).

- [ ] **Step 2: Tally top-1 accuracy and "right-card-wrong-frame" miss rate**

For each row, classify: correct, wrong-card, right-card-wrong-frame. The
expected outcome is that **right-card-wrong-frame** drops materially (this
is the failure mode pHash-on-name is designed to fix). Top-1 accuracy
should hold or improve.

If right-card-wrong-frame is **not** reduced, the pHash signal isn't doing
what it should — likely candidates: the input scans aren't well-aligned to
canonical 363×504, the name-region crop coordinates need adjustment for the
current scan distribution, or `imagehash.phash` is too coarse at 8×8. Open
an issue and stop before recalibrating thresholds.

- [ ] **Step 3: Recalibrate `CONF_GOOD_SCORE` / `CONF_GOOD_GAP`**

The combined score's distribution is compressed compared to pure Milo (15%
of the score now comes from a [0, 1] pHash term that's rarely near 0 or 1).
Sort the eval scans by combined score; pick thresholds that give roughly
the same `good/fair/poor` mix you had before, OR a deliberately stricter
`good` (e.g. capture only the cleanest 30%, not 24%, because the rerank
should be moving formerly-fair-but-correct scans into good).

Set them in `~/scan-and-identify/.env` on mtg-eye:

```env
SCAN_AND_IDENTIFY_CONF_GOOD_SCORE=0.50
SCAN_AND_IDENTIFY_CONF_GOOD_GAP=0.12
SCAN_AND_IDENTIFY_CONF_POOR_SCORE=0.40
SCAN_AND_IDENTIFY_CONF_POOR_GAP=0.04
```

(Use values derived from the eval — these are illustrative.)

```bash
ssh mtg-eye
cd ~/scan-and-identify
nano .env  # set the new values
docker compose up -d   # picks up new env without rebuild
```

- [ ] **Step 4: Verify health endpoint reports the new threshold-driven counts**

(The /health endpoint doesn't currently surface tier counts — out of scope
for this plan. Spot-check via 5-10 known-good scans through `/identify`
and confirm tiers look reasonable.)

- [ ] **Step 5: Final smoke**

```bash
cd ~/scan-and-identify
./scripts/smoke_test.sh   # if you keep KEY in env
```

Expected: all checks green.

---

## Self-review notes

- **No engine fork changes.** Constraint A satisfied — every change lives in `scan-and-identify`.
- **Milo decides orientation.** Constraint B satisfied — Task 4 step 3's `used_180` flag is read from Milo's emb_b > emb_a comparison and used to rotate the pHash input. No second orientation decision.
- **Tests cover the failure mode.** Task 4 step 1 specifically constructs a case where embedding-only ranking would pick the wrong product and rerank flips it.
- **No silent fallback.** Task 5 hard-fails on legacy catalogs. Op error message points at `scripts/refresh-catalog.sh`.
- **Threshold recalibration is in scope.** Task 8 forces explicit reflection on whether tier semantics changed.
- **Rollback path:** revert the server image AND the catalog (both are tagged in git history). The algo_key guard makes mismatched combos fail loudly at boot rather than silently.
