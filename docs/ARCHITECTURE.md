# Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│ Consumer website                                                 │
│   Owns batches, corrections, user accounts, scan storage.        │
└────────────────────────────┬─────────────────────────────────────┘
                             │ HTTP (Bearer auth, JSON in, JSON/CSV out)
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│ scan-and-identify (this repo, single Docker container)           │
│                                                                  │
│   FastAPI ──┬── /identify, /identify-batch                       │
│             ├── /search                                          │
│             ├── /products/{id} (+ resolve-sku)                   │
│             ├── /export/tcgplayer-csv                            │
│             └── /sets, /health                                   │
│                                                                  │
│   IdentifyPipeline ─── SetIndex (pre-sliced sub-catalogs)        │
│         │              + TCGStore (in-memory parquet join)       │
│         │              + BackRejector (optional)                 │
│         │                                                        │
│   Loaded at boot from:                                           │
│     - catalog.npz (baked into image)                             │
│     - 7 TCGplayer parquets (synced from R2)                      │
└────────────────────────────┬─────────────────────────────────────┘
                             │ Python imports
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│ CollectorVision (ipkstef/CollectorVision fork)                   │
│   Catalog, NeuralEmbedder, rotate_card_180. Nothing else.        │
└──────────────────────────────────────────────────────────────────┘
```

The API is stateless. Catalog NPZ is a read-only build artifact, refreshed
monthly. TCGplayer parquets sync from R2 at boot and live in pandas. All
per-request joins are dict lookups (O(1)) — nothing hits a database.

## Boot

`AppState.bootstrap`:

1. Sync the 7 TCGplayer parquets from R2 (skip files whose local mtime
   matches the R2 `LastModified`).
2. Load parquets into `TCGStore`. Build `_products_by_id` and
   `_skus_by_product` dicts.
3. Load the embedding catalog NPZ (`Catalog.load`).
4. Construct `NeuralEmbedder` — loads `milo.onnx`, 4 intra-op threads.
5. Pre-slice the catalog by `group_id` into one sub-Catalog per set
   (`SetIndex.build`). Numpy views share memory with the parent.
6. Load the canonical card-back PNG if configured; otherwise `BackRejector`
   is disabled.
7. Compose `IdentifyPipeline` and start uvicorn.

Cold-boot is ~3–5 seconds, dominated by catalog and parquet I/O.

## Per-request: `POST /identify`

1. Bearer token check.
2. `fetch_image(req.image_url)` — async httpx GET, 10s timeout, must return
   an image content-type. Non-200 or non-image → `400`.
3. `pipeline.identify(image, set_ids, top_k, rotation_invariant)`:
   - Resize to 448×448.
   - With `rotation_invariant=true`: embed image and its 180° rotation, run
     `set_index.search` for each, keep the orientation whose top-1 wins.
   - `back_rejector.is_back(emb, top_score)` → return `is_card_back=true`
     with empty candidates.
   - pHash rerank (see below): `combined = 0.85·cosine + 0.15·pHash`.
   - For each hit, join `store.product(pid)` for metadata + `printings`.
   - `confidence = thresholds.classify(top_score, gap_to_next)`.
4. Return JSON.

Sync inference work is offloaded to a thread pool via `asyncio.to_thread`
so the event loop stays responsive to concurrent requests. ONNX releases
the GIL during inference; multi-core hosts get real parallelism.

### pHash rerank

After Milo picks the winning orientation, the pipeline computes a 64-bit
DCT pHash on the name region (top ~15% of the card, with horizontal
margins) of the input image, in the same orientation Milo chose. For each
top-K candidate, it XORs the query pHash against the catalog's
pre-computed reference pHash for that `product_id` and converts the
Hamming distance into a similarity score in `[0, 1]`. Final ranking:

```
combined_score = 0.85 * cosine_similarity + 0.15 * (1 - hamming / 64)
```

The Milo embedding path itself is unchanged — same letterbox, same ONNX,
same orientation selection. pHash runs on a separate canonical 363×504
resize and never feeds back into the embedding.

## Per-request: `POST /export/tcgplayer-csv`

1. Auth.
2. If `merge_duplicates`, group rows by `(product_id, printing, condition,
   language)` and sum quantities. Price conflicts → `400` with details.
3. For each row: `store.product(pid)`, `store.resolve_sku(pid, ...)`,
   pick price (per-row override > formula > blank).
4. Return `200 text/csv`.

Pure dict lookups and string formatting. ~0.3 ms/row at scale.
