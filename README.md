# scan-and-identify

Stateless HTTP API that identifies cards from scanned images and returns TCGplayer product candidates with metadata. Wraps [CollectorVision](https://github.com/ipkstef/CollectorVision).

## Quick start

### 1. Configure

Create `.env` in the working directory:

```
SCAN_AND_IDENTIFY_API_KEY=replace-me
SCAN_AND_IDENTIFY_CATALOG_PATH=./catalogs/milo1-tcgplayer-mtg-2026-05.npz
R2_TCGPLAYER_BUCKET_ACCESS_KEY=...
R2_TCGPLAYER_BUCKET_SECRET_KEY=...
R2_TCGPLAYER_URL=https://<account>.r2.cloudflarestorage.com
```

### 2. Build the embedding catalog (one-time per TCGplayer drop)

```bash
scan-and-identify build-catalog \
    --products-parquet path/to/products.parquet \
    --out catalogs/milo1-tcgplayer-mtg-2026-05.npz
```

### 3. Run

```bash
scan-and-identify serve --port 8000
# or
docker run -d -p 8000:8000 --env-file .env \
    -v $PWD/catalogs:/app/catalogs scan-and-identify:latest
```

## API

All endpoints require `Authorization: Bearer $SCAN_AND_IDENTIFY_API_KEY`.

| Endpoint | Purpose |
|---|---|
| `GET  /health` | Liveness + catalog version |
| `GET  /sets` | All MTG sets with `is_current` flag |
| `POST /identify` | Single image URL → top-K candidates |
| `POST /identify-batch` | N image URLs → results per image |
| `GET  /products/{id}` | Full product metadata + all SKUs |
| `POST /products/{id}/resolve-sku` | (printing, condition, language) → sku_id + prices |
| `POST /export/tcgplayer-csv` | Confirmed rows → TCGplayer seller-template CSV |

**Full API reference + website-owned responsibilities: [`docs/API.md`](docs/API.md).**

Original design context lives at
`docs/superpowers/specs/2026-05-16-scan-and-identify-api-design.md` in the
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
