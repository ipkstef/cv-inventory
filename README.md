# cv-inventory

Stateless HTTP API that identifies cards from scanned images and returns TCGplayer product candidates with metadata. Wraps [CollectorVision](https://github.com/ipkstef/CollectorVision).

## Quick start

### 1. Configure

Create `.env` in the working directory:

```
CV_INVENTORY_API_KEY=replace-me
CV_INVENTORY_CATALOG_PATH=./catalogs/milo1-tcgplayer-mtg-2026-05.npz
R2_TCGPLAYER_BUCKET_ACCESS_KEY=...
R2_TCGPLAYER_BUCKET_SECRET_KEY=...
R2_TCGPLAYER_URL=https://<account>.r2.cloudflarestorage.com
```

### 2. Build the embedding catalog (one-time per TCGplayer drop)

```bash
cv-inventory build-catalog \
    --products-parquet path/to/products.parquet \
    --out catalogs/milo1-tcgplayer-mtg-2026-05.npz
```

### 3. Run

```bash
cv-inventory serve --port 8000
# or
docker run -d -p 8000:8000 --env-file .env \
    -v $PWD/catalogs:/app/catalogs cv-inventory:latest
```

## API

All endpoints require `Authorization: Bearer $CV_INVENTORY_API_KEY`.

| Endpoint | Purpose |
|---|---|
| `GET  /health` | Liveness + catalog version |
| `GET  /sets` | All MTG sets with `is_current` flag |
| `POST /identify` | Single image URL → top-K candidates |
| `POST /identify-batch` | N image URLs → results per image |
| `GET  /products/{id}` | Full product metadata + all SKUs |
| `POST /products/{id}/resolve-sku` | (printing, condition, language) → sku_id + prices |
| `POST /export/tcgplayer-csv` | Confirmed rows → TCGplayer seller-template CSV |

Full request/response shapes live in the design doc at
`docs/superpowers/specs/2026-05-16-cv-inventory-api-design.md` in the
CollectorVision repo.

## Development

```bash
uv venv -p 3.12
source .venv/bin/activate
uv pip install -e ".[dev]"
pytest tests/ -v
ruff check src/ tests/
```

The test suite uses synthetic fixtures and does not require R2 or a real catalog.
