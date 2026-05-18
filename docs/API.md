# cv-inventory API contract

Stateless HTTP API for scan-based card identification and TCGplayer-compatible
CSV export. Designed to be called from a consumer website that owns batches,
corrections, image storage, and the user-facing UI.

- All requests require `Authorization: Bearer $CV_INVENTORY_API_KEY`.
- All requests and responses are JSON, except `POST /export/tcgplayer-csv`
  which returns `text/csv`.
- All errors follow the shape `{"error": {"code": "string", "message": "string"}}`.

---

## Website responsibilities (NOT IN THE API)

Things the consumer website is expected to own. **These are not endpoints on
this server.** Documenting them here so integration teams know what to build.

### Batches

- Group scans into named batches. Track batch name, creator, timestamps, notes.
- A batch carries **pre-import defaults**: printing, condition, language.
  These propagate as per-row values when calling `/identify-batch` and
  `/export/tcgplayer-csv`.
- **Printing is immutable per batch.** TCGplayer enforces this because
  printing affects identification accuracy. Mirror it: once a batch is created,
  do not let the user change printing.
- Default language is English; the website may allow per-row override.
- Default condition can be changed per row.

### Image storage and lifecycle

- The website hosts scan images at URLs reachable by the API.
- Pre-signed URLs are recommended (we never store image bytes).
- Image lifecycle (retention, expiry) is the website's responsibility.
  TCGplayer's own product expires images at 60 days; pick a policy that
  suits the workflow.
- Reject landscape-oriented images at upload. TCGplayer requires portrait
  orientation regardless of whether the card is landscape; the embedder
  is trained on portrait crops.

### Import Sequence ID

- Every scan in a batch should carry an Import Sequence ID (typically the
  alphabetical position of the image filename in its folder, or a number
  assigned by the upstream sorter). Pass this through as the `id` field in
  `POST /identify-batch`. The API echoes it back unchanged so the website
  can correlate results to physical card positions in the stack.

### Confidence-based routing

- The API returns a `confidence` tier (`good`, `fair`, `poor`) per identify
  response. The website is responsible for routing rows to a "Matched" view
  or an "Unidentified / needs review" view based on these tiers, and for
  surfacing the appropriate UI affordances:
  - `good` — auto-accept; show as confirmed.
  - `fair` — show in Matched but flag for optional review.
  - `poor` — route to Unidentified; require manual correction before export.

### Manual correction tier

- When the user manually picks a candidate (from top-K or `/search`),
  the website records this as a "MANUAL" confidence in its own state.
  The API has no concept of MANUAL — once you call `/export/tcgplayer-csv`
  with a confirmed `product_id`, the row goes through regardless of how it
  was selected.

### Duplicate detection at scan time

- The API merges duplicate `(product_id, printing, condition, language)`
  rows at export time. The website may *also* dedupe at scan time for UX
  (e.g., show "you've already added this card 3 times in this batch"), but
  this is purely cosmetic — the canonical merge happens server-side.

### Listing-price refresh policy

- Reference prices in our parquet store update when the parquet refresh runs
  (typically daily). The website should communicate this cadence to users.
- The website chooses whether to use server-side `price_formula` in export,
  or compute prices itself and pass per-row `marketplace_price`. Both work.

### Push to TCGplayer inventory

- This API only produces CSV files. Pushing to TCGplayer (live or staged)
  is the website's job — typically by submitting the CSV through TCGplayer's
  seller-portal upload endpoint, or having the user download it and upload
  manually.

---

## Auth and errors

All endpoints require:

```
Authorization: Bearer $CV_INVENTORY_API_KEY
```

Missing or wrong key → `401 Unauthorized` with body:

```json
{"error": {"code": "http_401", "message": "Missing bearer token"}}
```

Error body convention for all non-2xx:

```json
{"error": {"code": "string", "message": "human-readable string"}}
```

The `code` is `http_<status>` for generic HTTPException; specific error types
(like `merge_price_conflict`) use a domain code and may include extra fields
inside `error`.

---

## Endpoints

### `GET /health`

Liveness probe + boot-time metadata.

**Response:**

```json
{
  "status": "ok",
  "catalog_version": "milo1-tcgplayer-mtg-2026-05",
  "catalog_size": 111100,
  "parquet_synced_at": "2026-05-17T07:47:41.990282+00:00"
}
```

`catalog_version` is the NPZ filename stem. `catalog_size` is the number of
embedded product_ids. `parquet_synced_at` is when the in-memory TCGStore was
last loaded (== container boot time in v1; we don't hot-reload yet).

---

### `GET /sets`

All TCGplayer sets (groups) for the configured category, with `is_current=true`
sets first.

**Response:**

```json
{
  "sets": [
    {"group_id": 24234, "name": "Commander: Tarkir: Dragonstorm", "abbr": "TDC", "is_current": true},
    {"group_id": 23445, "name": "Commander: Modern Horizons 3", "abbr": "M3C", "is_current": true},
    ...
  ]
}
```

Use to populate a set picker; pass `group_id` as `set_id` on `/identify`
to hard-lock identification to a single set.

---

### `POST /identify`

Identify a single scan image.

**Request:**

```json
{
  "image_url": "https://your-storage/scans/scan42.jpg",
  "set_id": 24234,
  "top_k": 5,
  "rotation_invariant": true
}
```

| Field | Type | Default | Notes |
|---|---|---|---|
| `image_url` | string | required | Must be HTTP(S), reachable from the container, return an image (any size; resized server-side). |
| `set_id` | int \| null | null | If set, hard-filters candidates to that TCGplayer set. Returns `404` if unknown. |
| `top_k` | int | 5 | 1-20. |
| `rotation_invariant` | bool | true | If true, embeds the image and its 180° rotation, keeps the orientation with the stronger top match. Adds ~50% latency. ADF scans benefit; pre-oriented uploads don't. |

**Response:**

```json
{
  "is_card_back": false,
  "confidence": "good",
  "candidates": [
    {
      "product_id": 591234,
      "score": 0.93,
      "name": "Lightning Bolt",
      "set_name": "Tarkir: Dragonstorm",
      "set_abbr": "TDC",
      "group_id": 24234,
      "collector_number": "146",
      "rarity": "Common",
      "image_url": "https://tcgplayer-cdn.tcgplayer.com/product/591234_200w.jpg"
    },
    ...
  ]
}
```

| Field | Type | Notes |
|---|---|---|
| `is_card_back` | bool | True when the card-back rejector fired. `candidates` is `[]` and `confidence` is `null` in this case. (Currently disabled until back-rejector PNG is bundled — always false today.) |
| `confidence` | `"good" \| "fair" \| "poor" \| null` | Tier of the top-1 candidate. Null when `candidates` is empty. See "Confidence" below. |
| `candidates` | array | Top-K best matches, sorted by score descending. |

**Errors:**

- `400` `Could not fetch image: ...` — image_url returned non-200 or non-image content.
- `404` `Unknown set_id N` — `set_id` doesn't exist in the catalog.

---

### `POST /identify-batch`

Identify N images in parallel.

**Request:**

```json
{
  "images": [
    {"id": "scan42", "image_url": "..."},
    {"id": "scan43", "image_url": "..."}
  ],
  "set_id": 24234,
  "top_k": 5,
  "rotation_invariant": true
}
```

`id` is opaque — the API echoes it back unchanged. Use it as your Import
Sequence ID.

**Response:**

```json
{
  "results": [
    {"id": "scan42", "is_card_back": false, "confidence": "good", "candidates": [...], "error": null},
    {"id": "scan43", "is_card_back": false, "confidence": "poor", "candidates": [...], "error": null},
    {"id": "scan44", "is_card_back": false, "confidence": null, "candidates": [], "error": "fetch failed: ..."}
  ]
}
```

Per-image errors do not fail the batch. The response always returns one
entry per input, in the same order.

---

### `GET /search`

TCGplayer "Find Match"-style product search. Used by the website's correction
UI when the top-K from `/identify` doesn't contain the right card.

**Query parameters:**

| Param | Type | Default | Notes |
|---|---|---|---|
| `name` | string | — | Case-insensitive substring of the product name. |
| `collector_number` | string | — | Case-insensitive exact match against the collector number. |
| `set_id` | int | — | Optional filter to one set. |
| `limit` | int | 20 | 1-100. |

**At least one of `name` or `collector_number` is required.** Empty query →
`400`. Sealed products are excluded.

**Response:**

```json
{
  "results": [
    {
      "product_id": 591234,
      "name": "Lightning Bolt",
      "set_name": "Tarkir: Dragonstorm",
      "set_abbr": "TDC",
      "group_id": 24234,
      "collector_number": "146",
      "rarity": "Common",
      "image_url": "https://tcgplayer-cdn.tcgplayer.com/product/591234_200w.jpg"
    },
    ...
  ]
}
```

Shape mirrors `/identify` candidates but with no `score` (no embedding ran).

---

### `GET /products/{product_id}`

Full product metadata + all SKU variants for one product.

**Response:**

```json
{
  "product_id": 591234,
  "name": "Lightning Bolt",
  "clean_name": "Lightning Bolt",
  "group_id": 24234,
  "set_name": "Tarkir: Dragonstorm",
  "set_abbr": "TDC",
  "collector_number": "146",
  "rarity": "Common",
  "is_sealed": false,
  "image_url": "https://tcgplayer-cdn.tcgplayer.com/product/591234_200w.jpg",
  "tcgplayer_url": "https://www.tcgplayer.com/product/591234/...",
  "skus": [
    {
      "sku_id": 9049325,
      "product_id": 591234,
      "printing": "Normal",
      "condition": "Near Mint",
      "language": "English",
      "market_price": 0.45,
      "low_price": 0.10,
      "mid_price": 0.30,
      "high_price": 1.99,
      "direct_low_price": 0.15
    },
    ...
  ]
}
```

`404` if `product_id` is unknown.

---

### `POST /products/{product_id}/resolve-sku`

Given the product and a variant combination, return the matching SKU.

**Request:**

```json
{"printing": "Normal", "condition": "Near Mint", "language": "English"}
```

Allowed values must match TCGplayer's exact strings:

- `printing`: "Normal", "Foil"
- `condition`: "Near Mint", "Lightly Played", "Moderately Played", "Heavily Played", "Damaged"
- `language`: "English", "Chinese (S)", "Chinese (T)", "French", "German",
  "Italian", "Japanese", "Korean", "Portuguese", "Russian", "Spanish"

**Response:**

```json
{
  "sku_id": 9049325,
  "market_price": 0.45,
  "low_price": 0.10,
  "mid_price": 0.30,
  "high_price": 1.99,
  "direct_low_price": 0.15
}
```

`404` if the product is unknown OR the variant combination doesn't exist as
a real SKU. Prices may be `null` when TCGplayer has no marketplace data.

---

### `POST /export/tcgplayer-csv`

Render the TCGplayer seller-bulk-upload CSV from a list of confirmed rows.

**Request:**

```json
{
  "rows": [
    {
      "product_id": 591234,
      "printing": "Normal",
      "condition": "Near Mint",
      "language": "English",
      "quantity": 1,
      "marketplace_price": 0.49
    },
    ...
  ],
  "merge_duplicates": true,
  "price_formula": {
    "reference": "market",
    "modifier": {"type": "percent", "value": 2.0}
  }
}
```

| Field | Type | Default | Notes |
|---|---|---|---|
| `rows` | array | required | At least one row. |
| `rows[].marketplace_price` | float \| null | null | Per-row override. Wins over `price_formula` for that row. |
| `merge_duplicates` | bool | **true** | Sum quantities for rows sharing `(product_id, printing, condition, language)`. Matches TCGplayer's merge-on-push semantics. |
| `price_formula` | object \| null | null | Server-side reference price + modifier. Applied only to rows that lack explicit `marketplace_price`. |
| `price_formula.reference` | enum | required | One of `market`, `low`, `mid`, `high`, `direct_low`. |
| `price_formula.modifier.type` | enum | — | `percent` or `fixed`. |
| `price_formula.modifier.value` | float | — | E.g., 2.0 for "+2%", -0.01 for "minus 1¢". |

**Response (success):**

`200 Content-Type: text/csv` with `Content-Disposition: attachment; filename="tcgplayer-export.csv"`.

CSV columns (in order, matching TCGplayer's seller template):

```
TCGplayer Id, Product Line, Set Name, Product Name, Title, Number, Rarity,
Condition, TCG Market Price, TCG Direct Low, TCG Low Price With Shipping,
TCG Low Price, Total Quantity, Add to Quantity, TCG Marketplace Price, Photo URL
```

**Error: merge price conflict**

If `merge_duplicates=true` and two rows for the same SKU specify different
`marketplace_price`, the API refuses to silently pick one:

```
400 application/json

{
  "error": {
    "code": "merge_price_conflict",
    "message": "merge_duplicates found 1 price conflict(s)",
    "conflicts": [
      {
        "sku_key": {
          "product_id": 1001,
          "printing": "Normal",
          "condition": "Near Mint",
          "language": "English"
        },
        "prices_seen": [1.49, 1.99]
      }
    ]
  }
}
```

The website should reconcile prices (let the user pick) and resubmit.
Alternatively pass `merge_duplicates: false` to keep duplicates as separate
rows.

**Other handling notes:**

- A row whose `(product_id, printing, condition, language)` doesn't resolve
  to a real SKU is emitted with blank `TCGplayer Id` and blank price columns.
  TCGplayer's importer will reject those rows individually; the rest of the
  file succeeds.
- Rows whose `product_id` doesn't exist at all are emitted as entirely blank
  rows. Same handling.
- `Product Line` is currently hardcoded to `Magic` (we only ship MTG in v1).

---

## Confidence

The `confidence` field on identify responses is computed from two signals:

- **`score`**: cosine similarity of the top-1 candidate. Always in `[0, 1]`.
- **`gap`**: `top_1.score - top_2.score`. Larger gap = more confident.

Tiers (default thresholds, calibrated against the 172-scan reference eval):

| Tier | Rule | Approximate behavior |
|---|---|---|
| `good` | `score ≥ 0.55 AND gap ≥ 0.15` | ~24% of typical batches. Very likely correct. Auto-accept. |
| `poor` | `score < 0.45 OR gap < 0.05` | Low confidence or ambiguous. Must be manually reviewed. |
| `fair` | everything in between | Likely correct; suggest review. |

Override via environment variables (no code change needed):

```
CV_INVENTORY_CONF_GOOD_SCORE=0.55
CV_INVENTORY_CONF_GOOD_GAP=0.15
CV_INVENTORY_CONF_POOR_SCORE=0.45
CV_INVENTORY_CONF_POOR_GAP=0.05
```

Tune these for your scanner and accuracy requirements.

---

## Boot ordering and operational notes

On container start the server:

1. Loads `.env` and validates required environment variables (fail-fast if missing).
2. Syncs the 7 TCGplayer parquets from R2 into the local cache (skipped files
   whose local mtime matches the R2 `LastModified`).
3. Loads the embedding catalog NPZ from `CV_INVENTORY_CATALOG_PATH`.
4. Loads the parquets into the in-memory `TCGStore`.
5. Pre-slices the catalog by `group_id` (one sub-catalog per TCGplayer set).
6. Loads the canonical card-back PNG if `--back-image` was passed (otherwise
   the back-rejector is disabled).
7. Starts FastAPI on `--host`/`--port`.

Total cold-boot time: ~3-5 seconds. After boot every request is hot.

The container is **stateless** — restart freely. State that should persist
(batches, user corrections, push history) lives on the website's side.

---

## Versioning

This document describes the API as implemented at the commit it was written
against. Until v1.0, the API may change without deprecation notices. Watch
the `Change log` section at the bottom of this file.

## Change log

- 2026-05-18 — Added `/search`, `confidence` field, `merge_duplicates`,
  `price_formula`. Documented website-owned responsibilities.
- 2026-05-16 — Initial API design (see `docs/superpowers/specs/` in the
  CollectorVision repo).
