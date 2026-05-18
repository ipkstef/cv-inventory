# scan-and-identify Architecture

The mental model in one page, then a tour of how the pieces compose.

---

## The three tiers

```
┌──────────────────────────────────────────────────────────────────┐
│   YOUR WEBSITE  (consumer; not in this repo)                    │
│                                                                  │
│   - Owns: batches, corrections, user accounts, scan storage      │
│   - Talks to scan-and-identify over HTTP with a shared bearer token   │
│   - See "Website Responsibilities" in docs/API.md                │
└────────────────────────────┬─────────────────────────────────────┘
                             │ HTTP (JSON in, JSON/CSV out)
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│   scan-and-identify  (this repo, Docker container)                   │
│                                                                  │
│   FastAPI app  ──┬── /identify, /identify-batch                  │
│                  ├── /search                                     │
│                  ├── /products/{id} (+ resolve-sku)              │
│                  ├── /export/tcgplayer-csv                       │
│                  └── /sets, /health                              │
│                                                                  │
│   ┌──────────────┐ ┌──────────────┐ ┌────────────────────────┐   │
│   │ IdentifyPipe │ │ TCGStore     │ │ SetIndex              │   │
│   │ embed+search │ │ in-memory    │ │ pre-sliced sub-       │   │
│   │              │ │ parquet join │ │ catalogs per set      │   │
│   └──────┬───────┘ └──────┬───────┘ └────────────────────────┘   │
│          │                │                                      │
│   ┌──────▼─────────────┐  │  Loaded at boot from:                │
│   │ BackRejector       │  │  - milo1-tcgplayer-mtg-YYYY-MM.npz   │
│   │ (optional)         │  │  - 7 parquets in R2 (synced on boot) │
│   └────────────────────┘  │                                      │
└──────────────────────────┼──────────────────────────────────────┘
                           │ Python imports
                           ▼
┌──────────────────────────────────────────────────────────────────┐
│   CollectorVision  (engine; ipkstef/CollectorVision fork)       │
│                                                                  │
│   We import three things:                                        │
│     - Catalog          (load NPZ, cosine search)                 │
│     - NeuralEmbedder   (Milo ONNX, 448x448 → 128-d vector)       │
│     - rotate_card_180  (for rotation-invariant search)           │
│                                                                  │
│   Nothing else from the engine is used in this app.              │
└──────────────────────────────────────────────────────────────────┘
```

---

## Why this shape

- **Stateless API.** All correction/batch/history state belongs to the consumer website. Lets the API scale horizontally, restart freely, and skip database migrations entirely.
- **Catalog NPZ is a build artifact.** Pre-computed monthly on mtg-eye. The server reads it; never writes. Cosine search over a 111k × 128 matrix is ~5ms on CPU.
- **TCGplayer parquets are read-only metadata.** Synced from R2 at boot. The whole 7-file set is ~50 MB compressed → ~150 MB pandas tables in RAM. Indexed by `product_id` and `(product_id, printing_id, condition_id, language_id)` once at construction; every lookup is O(1).
- **TCGplayer-keyed end-to-end.** `product_id` is the primary key from scan to CSV — no mapping to Scryfall or other catalogs. Matches the data flow your seller portal expects.

---

## Boot sequence

`AppState.bootstrap(config, parquet_cache, back_image)` runs in this order:

1. **Sync parquets from R2** (`r2_sync.sync_parquets`). For each of the 7 files (`products`, `skus`, `groups`, `conditions`, `printings`, `languages`, `rarities`), `head_object` the remote `LastModified` and skip if local cache is fresher; otherwise download.
2. **Load parquets into pandas + index** (`TCGStore.load`). Builds `_products_by_id` and `_skus_by_product` dicts so all per-request joins are dict lookups, not pandas filters.
3. **Load embedding catalog NPZ** (`Catalog.load`). Reads `card_ids`, `embeddings`, `source`, `embedder_spec`. The catalog knows which embedder built it.
4. **Construct `NeuralEmbedder`** — loads `milo.onnx` (5 MB), warms onnxruntime with 4 intra-op threads.
5. **Pre-slice the catalog by `group_id`** (`SetIndex.build`). For each unique set, slice `catalog.embeddings` and `catalog.card_ids` into a sub-Catalog. Numpy views share memory with the parent — total RAM stays at ~55 MB regardless of how many sets exist.
6. **Load the canonical card-back image** if `--back-image` was passed; embed it once and store as the comparison vector. If absent, `BackRejector` initializes as disabled.
7. **Compose `IdentifyPipeline`** from `(embedder, set_index, store, back_rejector, confidence_thresholds)`.
8. **FastAPI starts listening.**

Cold-boot time: ~3-5 seconds (catalog and parquet loads dominate).

After boot, every request is hot — no lazy loads, no warm-up.

---

## Per-request flow: `POST /identify`

```
┌─────────────────────────────────────────────────────────────────┐
│ 1. Bearer token check                                          │
│    Wrong/missing → 401 with {"error": {...}}                    │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│ 2. fetch_image(req.image_url)                                  │
│    Async httpx GET, 10s timeout, validate it's an image        │
│    Non-200 or non-image → 400 "Could not fetch image: ..."      │
└──────────────────────────────┬──────────────────────────────────┘
                               │  PIL.Image (RGB, any size)
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│ 3. pipeline.identify(image, set_id, top_k, rotation_invariant) │
│                                                                 │
│    a. img = image.convert("RGB").resize((448, 448))             │
│    b. (rotation_invariant=true)                                 │
│         emb_a = embedder.embed(img)        # ~50ms              │
│         emb_b = embedder.embed(rotate_card_180(img))  # ~50ms   │
│         hits_a = set_index.search(emb_a, set_id, top_k)         │
│         hits_b = set_index.search(emb_b, set_id, top_k)         │
│         keep the orientation with stronger top-1                 │
│    c. back_rejector.is_back(emb, top_score)?                    │
│         → is_card_back=true, candidates=[]                       │
│    d. for each hit: store.product(pid) → enrich with metadata   │
│    e. confidence = thresholds.classify(top_score, gap_to_next)  │
│                                                                 │
│    Returns IdentifyResult(is_card_back, candidates, confidence) │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│ 4. Route serializes dataclass → JSON, returns 200               │
└─────────────────────────────────────────────────────────────────┘
```

**Total latency:** ~600ms with `rotation_invariant=true`, ~300ms without.

Breakdown of the rotation-invariant path:
- Image fetch over HTTPS: 100-300ms (variable, dominates on small images from slow storage)
- Embed × 2: ~100ms (50ms each, sequential because Milo is batch-size-1)
- Cosine search × 2: ~10ms (negligible)
- Metadata join × top_k: <1ms (all dict lookups)
- JSON serialization: <1ms

**The bottlenecks worth knowing:**
- Image fetch is unbounded and outside our control. If consumer storage is slow, requests are slow.
- Embed is CPU-bound and serializes. With 16 cores, we can theoretically serve ~16 concurrent identifies at once before saturating. Beyond that, queue.
- For batch endpoints, downloads parallelize (gather), embeds still serialize. Useful gain on the network step, no gain on the CPU step until Milo's ONNX is re-exported with a dynamic batch dim.

---

## Per-request flow: `POST /export/tcgplayer-csv`

```
1. Auth check
2. rows = [r.model_dump() for r in req.rows]
3. if merge_duplicates:
       rows = merge_duplicate_rows(rows)
       on price conflict → 400 with conflict details
4. for each row:
       product = store.product(pid)           # dict lookup
       sku = store.resolve_sku(pid, ...)      # dict lookup
       marketplace_price = row.marketplace_price
                          or apply_price_formula(formula, sku)
       write CSV row
5. Return 200 text/csv with attachment Content-Disposition
```

Pure dict lookups and string formatting. Sub-millisecond per row. A 1000-row export is ~5ms server-side.

---

## File-to-responsibility map

| Path | Owns |
|---|---|
| `src/scan_and_identify/config.py` | Env-var loading + validation |
| `src/scan_and_identify/tcgplayer/r2_sync.py` | R2 → local cache with freshness check |
| `src/scan_and_identify/tcgplayer/store.py` | In-memory join layer (the most-touched file) |
| `src/scan_and_identify/tcgplayer/seller_csv.py` | TCGplayer seller-template CSV + merge + formula |
| `src/scan_and_identify/image_fetch.py` | Async HTTP → PIL Image |
| `src/scan_and_identify/back_rejector.py` | Cosine gate vs canonical card-back |
| `src/scan_and_identify/set_index.py` | Pre-sliced sub-catalogs per group_id |
| `src/scan_and_identify/pipeline.py` | Compose embedder + index + store + back-rejector |
| `src/scan_and_identify/catalog_build.py` | Build/download CLI logic |
| `src/scan_and_identify/cli.py` | argparse entry point (`scan-and-identify`) |
| `src/scan_and_identify/server/state.py` | Singleton boot + dependency container |
| `src/scan_and_identify/server/auth.py` | Bearer-token middleware |
| `src/scan_and_identify/server/schemas.py` | All Pydantic request/response models |
| `src/scan_and_identify/server/app.py` | Route wiring + error translation |
| `scripts/eval_review.py` | Generate HTML accuracy report from a folder of scans |
| `scripts/smoke_test.sh` | Boot Docker + curl every endpoint |

---

## Why we DON'T have…

- **A database.** Stateless. Batches/corrections live on the website.
- **Corner detection.** ADF scans are pre-cropped; resize-to-448 is enough.
- **Session middleware / cookies.** Bearer token in header is the only auth.
- **A search-by-set-name endpoint.** `/sets` returns the full list (450 items, small); the website filters client-side.
- **A "feedback" endpoint for wrong identifications.** No model retraining loop yet. If the website wants to collect corrections for later model improvement, it can store them itself; we'll add an ingest endpoint when there's somewhere to send them.
- **Hot reload of the catalog.** Restart the container. Catalog is monthly; restart cost is ~5 seconds.

---

## Where the data actually lives

| Data | Source of truth | Lives in scan-and-identify as |
|---|---|---|
| Card embeddings | Built monthly on mtg-eye from TCGplayer CDN images | NPZ at `SCAN_AND_IDENTIFY_CATALOG_PATH`, baked into Docker image |
| Card metadata (names, sets, rarities, prices) | R2 bucket `tcgplayerapi/<category>/*.parquet` (maintained by separate process) | In-memory pandas tables, re-synced at every container boot |
| Scan images | Consumer website's storage (S3, R2, whatever) | Never stored — fetched per request, processed, discarded |
| Batches / corrections / user state | Consumer website's database | Never stored — passed through in requests |
| API logs / errors | Container stdout | Operator decides (CloudWatch, Loki, plain disk, etc.) |
