# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A stateless HTTP API that identifies cards from scanned images and emits TCGplayer-compatible CSV exports. Wraps the [CollectorVision](https://github.com/ipkstef/CollectorVision) inference engine with a TCGplayer metadata layer.

**The full design context, API contract, and operational runbook live in `docs/`:**
- `docs/API.md` — endpoint reference + the "Website Responsibilities" section every integrator needs
- `docs/ARCHITECTURE.md` — three-tier mental model + boot sequence + per-request flow
- `docs/OPERATIONS.md` — catalog refresh, deployment, mtg-eye build dance
- The original spec lives in the sibling repo at `CollectorVision/docs/superpowers/specs/2026-05-16-scan-and-identify-api-design.md`

## Common commands

Setup (Python 3.12+):
```bash
uv venv -p 3.12
source .venv/bin/activate
uv pip install -e ".[dev]"
```

Tests + lint:
```bash
uv run pytest tests/ -v
uv run pytest tests/test_search.py::test_search_endpoint_returns_matches -v   # single test
uv run ruff check src/ tests/
uv run ruff format src/ tests/
```
The test suite uses synthetic fixtures (`tests/fixtures/synthetic.py`) and needs neither R2 nor a real catalog. Always green expected.

Run the server locally:
```bash
cp ../CollectorVision/.env .env   # or compose your own; see docs/API.md for env vars
# (add SCAN_AND_IDENTIFY_API_KEY and SCAN_AND_IDENTIFY_CATALOG_PATH)
scan-and-identify serve --port 8000
```

Docker:
```bash
docker build -t scan-and-identify:latest .
docker run -d -p 8000:8000 --env-file .env -v $PWD/catalogs:/app/catalogs scan-and-identify:latest
```

Build a catalog NPZ (one-shot, runs on mtg-eye in practice — see `docs/OPERATIONS.md`):
```bash
scan-and-identify download-images --products-parquet path/to/products.parquet --image-cache /tmp/imgs --rate 40 --concurrency 32
scan-and-identify build-catalog   --products-parquet path/to/products.parquet --image-cache /tmp/imgs --out catalogs/<file>.npz --rate 40 --concurrency 32
```

## Architecture in one paragraph

A FastAPI app (`server/app.py`) wraps a pipeline (`pipeline.py`) that uses an `IdentifyPipeline` composed of: `NeuralEmbedder` (from CollectorVision), `SetIndex` (pre-sliced sub-catalogs for set-lock searches), `TCGStore` (in-memory join layer over 7 TCGplayer parquets), and `BackRejector` (currently disabled until a canonical card-back PNG is bundled). At boot, `AppState.bootstrap()` (`server/state.py`) syncs parquets from R2 via boto3, loads the catalog NPZ, and constructs every singleton. The result: per-request latency ~600ms with `rotation_invariant=true`, mostly embed time on CPU.

## Conventions that aren't obvious

- **Stateless API.** Server holds NO per-request state. Batches, corrections, user accounts live on the consumer website. Don't add session middleware, don't add a database. See "Website Responsibilities" in `docs/API.md`.
- **Pydantic only at HTTP boundaries.** Pipeline functions return plain dataclasses (`pipeline.IdentifyResult`, `pipeline.Candidate`). Route handlers convert to dicts; FastAPI's `response_model` does the Pydantic validation. Don't pollute the pipeline with Pydantic.
- **TCGStore returns dicts, not models.** Methods like `store.product(pid)` return `dict | None`. Route handlers shape these to the response schema. Keeps the store HTTP-agnostic so it's testable without FastAPI.
- **Errors translated at the route, not raised through.** Modules raise typed errors (`FetchError`, `MergePriceConflict`, `KeyError` from `SetIndex.search`). Routes are the single place that maps each to an `HTTPException` with the right status code. The global `HTTPException` handler in `app.py` formats them as `{"error": {"code": ..., "message": ...}}`.
- **Fail-fast at boot.** `Config.from_env()` validates required env vars and raises `ConfigError` if anything is missing. A misconfigured container should crash on startup, not 30 seconds into the first request.
- **Polymorphic catalog IDs.** The engine's `Catalog.card_ids` is stored as `<U36` strings in our NPZ (TCGplayer product_ids stringified). We cast to `int` at the seam (`SetIndex.search`, `pipeline.identify` loop). The engine's other supported format (`(N, 16) uint8` UUID) is not used.
- **Sealed products are excluded from the catalog.** `catalog_build.build_catalog` filters `is_sealed=False`. `TCGStore.search_products` also excludes sealed. Don't change either without thinking about whether the website wants to find sealed product.

## Test layout

- `tests/fixtures/synthetic.py` builds a tiny synthetic catalog (4 products) + parquets (5 products, 16 SKUs) used by every test. Two pytest fixtures in `conftest.py` expose them: `synthetic_catalog` (Path to NPZ), `synthetic_parquets` (Path to dir of parquets).
- `AppState.bootstrap_for_tests(api_key, catalog_path, parquet_dir)` builds the singleton WITHOUT R2 sync. Use it in HTTP tests via `TestClient(create_app(state))`.
- Async tests use `pytest-asyncio` in auto mode (configured in `pyproject.toml`). HTTP mocking uses `respx`.
- R2 mocking uses `moto[s3]` with `MOTO_S3_CUSTOM_ENDPOINTS=http://fake.r2.cloudflarestorage.com` env var (see `tests/test_r2_sync.py`). Custom endpoints need this or moto throws recursion errors.

## Things that are deliberately missing

- No database. No migrations. No sessions.
- No corner-detection (Cornelius). ADF scans are pre-cropped; we skip detection and just resize-to-448 before embedding. If you ever need to support phone-photo uploads, wire in `collector_vision.NeuralCornerDetector` + `DetectionResult.dewarp()` in `pipeline.py`.
- No card-back PNG bundled. `BackRejector` initializes disabled (never flags). Drop a 448×448 canonical at e.g. `src/scan_and_identify/data/mtg_card_back.png` and pass `--back-image` to enable.
- No hot-reload of catalog. Restart the container.
- No per-key auth or rate limiting. Single shared bearer token.
- No support for non-MTG categories yet. The shape generalizes (set `SCAN_AND_IDENTIFY_TCGPLAYER_CATEGORY=N`) but `Product Line: "Magic"` is hardcoded in the seller CSV — needs a category-to-product-line lookup before adding Pokémon etc.

## When to touch the engine fork vs this repo

The engine (`ipkstef/CollectorVision`, pinned in `pyproject.toml`) provides three things we use: `Catalog`, `NeuralEmbedder`, `rotate_card_180`. If you find a bug in any of those, fix it in the engine repo and bump the git pin in `pyproject.toml`. Everything else (TCGplayer data, API shape, business logic) belongs here.

Upstream PR candidates that have come up:
- Re-export Milo's ONNX with a dynamic batch dim (would speed up `build-catalog` ~10x).
- A `Catalog.with_index_subset(mask)` API to replace our hand-rolled `SetIndex`.
- Better card-back rejection built into the engine.
