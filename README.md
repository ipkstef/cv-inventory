# scan-and-identify

Stateless HTTP API that identifies cards from scanned images and emits
TCGplayer-compatible CSV exports. Wraps the
[CollectorVision](https://github.com/ipkstef/CollectorVision) inference engine
with a TCGplayer metadata layer.

- **Architecture:** [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- **API contract + website responsibilities:** [`docs/API.md`](docs/API.md)
- **Operations runbook:** [`docs/OPERATIONS.md`](docs/OPERATIONS.md)

## Deploy

Two paths. Pick whichever fits.

### Production (recommended): pull pre-built image

The GitHub Actions "Build image" workflow publishes images to
`ghcr.io/ipkstef/scan-and-identify`. Use the published image directly — no
git clone with LFS, no Docker build, no Python toolchain required on the host.

```bash
# One-time host setup
sudo apt update && sudo apt install -y docker.io docker-compose-plugin

# Pull the docker-compose.yml + .env.example. Two files is all you need.
mkdir scan-and-identify && cd scan-and-identify
curl -fsSLO https://raw.githubusercontent.com/ipkstef/scan-and-identify/main/docker-compose.yml
curl -fsSLO https://raw.githubusercontent.com/ipkstef/scan-and-identify/main/.env.example
cp .env.example .env

# Fill in .env (see "Configuration" below)
nano .env

# Pull and run
docker compose pull && docker compose up -d

# Verify
curl -H "Authorization: Bearer $YOUR_KEY" http://localhost:8000/health
```

To deploy a new image after CI builds one: `docker compose pull && docker compose up -d`.

### Local development / first build

When you're hacking on the code or don't want to wait on CI:

```bash
sudo apt install -y docker.io docker-compose-plugin git git-lfs
git lfs install
git clone https://github.com/ipkstef/scan-and-identify.git
cd scan-and-identify
cp .env.example .env
nano .env
docker compose up -d --build   # builds the image locally
```

## Configuration

Required env vars (see `.env.example` for the full list):

| Var | Notes |
|---|---|
| `SCAN_AND_IDENTIFY_API_KEY` | Shared bearer token. Generate with `openssl rand -hex 32`. |
| `SCAN_AND_IDENTIFY_CATALOG_PATH` | Default `/app/catalogs/catalog.npz` (the baked-in catalog). |
| `R2_TCGPLAYER_BUCKET_ACCESS_KEY` | R2 credentials for syncing TCGplayer parquets at boot. |
| `R2_TCGPLAYER_BUCKET_SECRET_KEY` | Same. |
| `R2_TCGPLAYER_URL` | `https://<account-id>.r2.cloudflarestorage.com` |

R2 credentials come from the operator who owns the `tcgplayerapi` bucket.
If that's you, find them in your Cloudflare R2 dashboard. If not, ask the
operator.

## API

All endpoints require `Authorization: Bearer $SCAN_AND_IDENTIFY_API_KEY`.

| Endpoint | Purpose |
|---|---|
| `GET  /health` | Liveness + catalog version + build timestamp |
| `GET  /sets` | All MTG sets with `is_current` flag |
| `GET  /search` | Search products by name/collector_number; powers "Find Match" UI |
| `POST /identify` | Single image URL → top-K candidates with confidence tier |
| `POST /identify-batch` | N image URLs → results per image |
| `GET  /products/{id}` | Full product metadata + all SKUs |
| `POST /products/{id}/resolve-sku` | (printing, condition, language) → sku_id + prices |
| `POST /export/tcgplayer-csv` | Confirmed rows → TCGplayer seller-template CSV |

**Full reference:** [`docs/API.md`](docs/API.md). The "Website Responsibilities"
section there is required reading for anyone integrating against this API.

## Catalog refresh (monthly)

When TCGplayer ships new products (new sets), refresh the catalog:

```bash
# On any host with Docker + the cloned repo + .env
scripts/refresh-catalog.sh
git add catalogs/catalog.npz
git commit -m "Refresh catalog"
git push
# Then: go to GitHub Actions → "Build image" → Run workflow
# Then on production: docker compose pull && docker compose up -d
```

The refresh runs in the existing Docker image, so the build host doesn't need
a Python venv or AWS CLI. The image cache (`~/scan-and-identify-cache/` by
default) persists between runs — typical monthly refresh only downloads the
few hundred new product images, then re-embeds the full catalog (~75 min CPU).

## Development

```bash
uv venv -p 3.12
source .venv/bin/activate
uv pip install -e ".[dev]"
pytest tests/ -v
ruff check src/ tests/
```

The test suite uses synthetic fixtures and needs neither R2 nor a real
catalog. Always green expected.
