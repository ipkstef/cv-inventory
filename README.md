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

## Identification accuracy & how the disambiguation works

Magic cards are visually hard to identify *at the product level*. A single
card name (e.g. "Chain Lightning") can exist as 6+ separate `product_id`s
across sets, with near-identical artwork. A single set can have the same
card in multiple frame treatments (base, borderless, showcase, retro frame),
each its own `product_id`. Pure visual embedding gets you to the right
*card* but not always the right *product*.

This API uses **three orthogonal disambiguation signals**, each addressing
a different failure mode:

| Signal | Where | Catches |
|---|---|---|
| **Milo embedding** (Visual, 128-dim) | every request | Cards that look generally distinct from each other |
| **Name-region pHash** (Visual, 64-bit DCT) | every request, blended at 15% weight | Different cards whose full-card embeddings collide by accident |
| **Set lock** (Semantic, operator-provided) | when website passes `set_ids` | Cross-set reprints of the same card (same art, different `product_id`) |

The first two run unconditionally — see [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
for the rerank formula. The third requires the consumer website to pass
`set_ids: list[int]` on the identify request when the operator knows what
set(s) the physical stack came from.

### Measured impact: set lock A/B (DMR, 76 scans, 2026-05-21)

A real batch of 76 Dominaria Remastered scans, sent twice — once without
`set_ids`, once with `set_ids=[17670]`:

| Metric | Unlocked | Locked | Δ |
|---|---|---|---|
| `good` tier (auto-accept) | 7 | **12** | **+71%** |
| `fair` tier (suggest review) | 27 | **38** | **+41%** |
| `poor` tier (must review) | 42 | **26** | **−38%** |
| `good + fair` combined | 45% (34/76) | **66% (50/76)** | **+21 pp** |
| Median gap (top-1 minus top-2) | 0.047 | **0.085** | **+80%** |
| Top-1 `product_id` flipped by lock | — | **24/76 (32%)** | — |
| Tier upgrades / downgrades | — | 22 / 2 | 11:1 ratio |

**32% of scans had their top-1 product_id swapped** to a different
`product_id` after locking — every one of those is a cross-set reprint
the unlocked embedder couldn't resolve. The 2 downgrades are scans where
within-DMR variant clusters are tighter than the cross-set leak that
removal exposed: a known, expected edge case.

### What we have not measured

To keep this section honest about what we know vs. don't:

- **Per-scan accuracy (top-1 == ground truth) is not measured.** Logs have
  product_ids but no ground-truth labels. The website's correction UI
  is the only source of ground truth and we haven't piped that back yet.
- **pHash's independent contribution is not measured.** The architecture
  exists to catch a class of failure (different cards whose embeddings
  collide) but we have no pre-pHash production traffic to compare against.
  When the failure mode in a batch is *same-card cross-printing*, pHash
  on the name region cannot help — both candidates share an identical
  name region.
- **Confidence tier thresholds** (`good`/`fair`/`poor`) are calibrated
  against a 172-scan eval done before this API existed. They may drift
  as catalog refreshes change the score distribution.

### Practical implication for the consumer website

If you have *any* hint about what set the operator is scanning — a batch
metadata field, a UI set picker, the box label — pass it as `set_ids`.
The 21-point swing in auto-acceptable tier rate is the single biggest
lever you have over scan quality, by a wide margin.

If a batch genuinely spans multiple known sets, pass them all:
`set_ids=[17670, 23445]`. Union semantics; strict (404 on unknown id).

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
