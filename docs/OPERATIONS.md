# cv-inventory Operations Runbook

Everything you need to deploy, refresh, and observe the running system.

---

## Environment variables

| Var | Required | Default | Notes |
|---|---|---|---|
| `CV_INVENTORY_API_KEY` | yes | — | Shared bearer token. Generate via e.g. `openssl rand -base64 48`. |
| `CV_INVENTORY_CATALOG_PATH` | yes | — | Absolute path to the `.npz` catalog. In Docker, baked in at `/app/catalogs/<file>.npz`. |
| `R2_TCGPLAYER_BUCKET_ACCESS_KEY` | yes | — | R2 access key for the `tcgplayerapi` bucket. |
| `R2_TCGPLAYER_BUCKET_SECRET_KEY` | yes | — | R2 secret key. |
| `R2_TCGPLAYER_URL` | yes | — | R2 endpoint, e.g. `https://<account-id>.r2.cloudflarestorage.com`. |
| `CV_INVENTORY_TCGPLAYER_CATEGORY` | no | `1` | TCGplayer category id. `1` = MTG. |
| `CV_INVENTORY_CONF_GOOD_SCORE` | no | `0.55` | Confidence tier threshold (top-1 score). |
| `CV_INVENTORY_CONF_GOOD_GAP` | no | `0.15` | Confidence tier threshold (gap to top-2). |
| `CV_INVENTORY_CONF_POOR_SCORE` | no | `0.45` | Below this score → `poor`. |
| `CV_INVENTORY_CONF_POOR_GAP` | no | `0.05` | Below this gap → `poor`. |
| `AWS_DEFAULT_REGION` | no | `auto` | Set by boto3 for R2; usually not needed. |

The container will fail fast at boot with `ConfigError: Missing required environment variables: ...` if any required var is unset. Wire that into your alerting.

---

## Deployment

### Image build

```bash
# On any machine with the source + a real catalog NPZ in catalogs/:
docker build -t cv-inventory:$(git rev-parse --short HEAD) .
docker tag  cv-inventory:$(git rev-parse --short HEAD) cv-inventory:latest
```

The Dockerfile copies `catalogs/` into the image at build time, so the catalog is baked in. **Tagging convention:** use git SHA for releases (`cv-inventory:abc123f`) so rollback is trivial, plus `latest` as a convenience tag.

### Pushing to a registry

```bash
# GHCR example
docker tag cv-inventory:abc123f ghcr.io/ipkstef/cv-inventory:abc123f
docker push ghcr.io/ipkstef/cv-inventory:abc123f
```

### Running

```bash
docker run -d --name cv-inventory \
  --restart=unless-stopped \
  -p 8000:8000 \
  --env-file /etc/cv-inventory/.env \
  cv-inventory:abc123f
```

The `.env` file should contain the required vars listed above. **Never bake secrets into the image** — the catalog NPZ is the only artifact baked in. Everything else comes from `.env` at runtime.

### Resource sizing

| Resource | Need | Why |
|---|---|---|
| CPU | 4-8 cores | Milo embedder pegs ~4 cores; need headroom for parallel requests |
| RAM | 1-2 GB | Catalog (~55 MB) + parquets (~150 MB) + Python/onnxruntime overhead |
| Disk | 500 MB image + ~150 MB R2 cache | Image is heaviest from baked-in catalog + onnxruntime |
| Network | minimal | Image-fetch latency is bounded by consumer storage, not by us |

Single container handles **~10-20 RPS** depending on rotation-invariance and image-fetch latency. To go higher: scale horizontally with N replicas behind any load balancer. The API is stateless — round-robin LB is fine, no sticky sessions needed.

---

## Catalog refresh (monthly)

TCGplayer ships new products monthly; refresh the catalog so they're identifiable.

**This runs on mtg-eye, not locally.** See `~/.claude/projects/-Users-stefanosamanuel-CollectorVision/memory/feedback_catalog_build_host.md` for context.

### One-shot refresh

```bash
# On mtg-eye:
ssh mtg-eye

cd /tmp/cv-inventory && git pull origin main && uv pip install -q -e .

# 1. Pull the latest products.parquet from R2
uv run python -c "
import os, boto3
from dotenv import load_dotenv; load_dotenv()
s3 = boto3.client('s3',
  endpoint_url=os.environ['R2_TCGPLAYER_URL'],
  aws_access_key_id=os.environ['R2_TCGPLAYER_BUCKET_ACCESS_KEY'],
  aws_secret_access_key=os.environ['R2_TCGPLAYER_BUCKET_SECRET_KEY'],
  region_name='auto')
s3.download_file('tcgplayerapi', '1/products.parquet', '/tmp/products.parquet')
print('done')
"

# 2. Download any new images (existing cache is reused → typically only a few hundred new fetches)
nohup .venv/bin/cv-inventory download-images \
  --products-parquet /tmp/products.parquet \
  --image-cache /tmp/cv-build/imgs \
  --rate 40 --concurrency 32 \
  > /tmp/cv-build/download.log 2>&1 &

# Wait for completion. Monthly delta is typically a few hundred new cards.
# Check: tail -f /tmp/cv-build/download.log

# 3. Re-embed everything (no network; pure CPU on cached images)
MONTH=$(date +%Y-%m)
nohup .venv/bin/cv-inventory build-catalog \
  --products-parquet /tmp/products.parquet \
  --image-cache /tmp/cv-build/imgs \
  --out /tmp/cv-build/milo1-tcgplayer-mtg-${MONTH}.npz \
  --rate 40 --concurrency 32 \
  > /tmp/cv-build/embed.log 2>&1 &

# This takes ~75-90 min on mtg-eye for the full 111k catalog. Cached images = no network.
# Check: tail -f /tmp/cv-build/embed.log
```

When the build finishes, pull the NPZ back to your laptop:

```bash
scp mtg-eye:/tmp/cv-build/milo1-tcgplayer-mtg-${MONTH}.npz /Users/stefanosamanuel/cv-inventory/catalogs/
```

### Then rebuild + redeploy

```bash
cd ~/cv-inventory
docker build -t cv-inventory:$(git rev-parse --short HEAD) .
# push to registry, deploy on production host, drain old container
```

### Performance tuning during refresh

- The download phase is rate-limited (we observed clean 40 req/s on Cloudflare-fronted TCGplayer CDN with zero throttling). Push higher cautiously — monitor for 429/503.
- The embed phase is CPU-bound and serial because Milo's ONNX is exported with `batch_size=1`. ~25 products/sec is the ceiling without an engine-side fix.

### Resume if the build dies

Both `download-images` and `build-catalog` are resumable. The image cache (`/tmp/cv-build/imgs`) persists between runs, so re-running skips anything already downloaded. The embed pass re-embeds everything (we don't incrementally update the NPZ), but downloads are the slow part — embed-only after a crash is ~75 min vs ~90 min for the full pipeline.

---

## Observability

### What to monitor

| Signal | Where | Alert if |
|---|---|---|
| Container alive | Health check (Docker, k8s liveness) | `/health` returns non-200 |
| Boot succeeded | First `/health` reply | Takes >30s (something is wrong) |
| Catalog age | `/health` `catalog_version` field | Doesn't include the current YYYY-MM after the 5th of the month |
| Parquet sync age | `/health` `parquet_synced_at` field | Older than container restart cadence + 1 day |
| Identify latency | App logs (uvicorn access) | p95 > 2s sustained — image fetch is probably slow |
| Identify errors | App logs | Any 5xx; non-trivial rate of 400 |
| 401s | App logs | Burst of 401s = key rotation needed or website misconfigured |
| Memory | Container metrics | RSS climbing > 2 GB (we shouldn't allocate over time) |

### Useful curl commands

```bash
# Liveness
curl -fsS -H "Authorization: Bearer $KEY" http://localhost:8000/health

# Sample identify with a known-good TCGplayer image
curl -s -X POST -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"image_url":"https://tcgplayer-cdn.tcgplayer.com/product/218276_in_1000x1000.jpg","top_k":3}' \
  http://localhost:8000/identify | jq

# Set list (first 3)
curl -s -H "Authorization: Bearer $KEY" http://localhost:8000/sets | jq '.sets[:3]'
```

`scripts/smoke_test.sh` runs all of these for you.

### Logs

The container logs to stdout in uvicorn's default format. To capture:

```bash
docker logs -f cv-inventory
docker logs cv-inventory 2>&1 | grep -iE "error|warning"
```

In production you'd ship these to your log aggregator of choice. There's nothing structured-logging-aware in cv-inventory today; logs are plain text.

---

## Rotating the API key

Generate a new key, update the website's secret, then redeploy with `CV_INVENTORY_API_KEY=<newkey>`:

```bash
NEWKEY=$(openssl rand -base64 48 | tr -d '=+/' | head -c 48)
echo "CV_INVENTORY_API_KEY=$NEWKEY"
# Update website secret, then:
docker stop cv-inventory && docker rm cv-inventory
docker run -d --name cv-inventory --restart=unless-stopped -p 8000:8000 \
  --env-file /etc/cv-inventory/.env  cv-inventory:latest
```

No graceful rotation supported in v1 (single shared key). If you need zero-downtime rotation, you'd want per-key auth — not implemented.

---

## Common failures and what they mean

| Symptom | Cause | Fix |
|---|---|---|
| Container exits on boot with `ConfigError` | Missing env var | Check `.env`; see required vars table above |
| Container exits with `FileNotFoundError: catalog.npz` | `CV_INVENTORY_CATALOG_PATH` points at a non-existent file | Verify path; rebuild image if catalog was supposed to be baked in |
| Boot hangs >30s | R2 sync is timing out | Check `R2_TCGPLAYER_URL`, network reachability, credentials |
| All `/identify` requests return 400 "Could not fetch image" | Consumer storage is unreachable from the container | Network/firewall; pre-signed URLs may have expired |
| `/identify` returns wrong card consistently for one set | Catalog is stale for that set | Refresh catalog (see above) |
| Slow `/identify` (>2s) | Image fetch from consumer storage is slow | Investigate storage latency; pre-warm with `--rotation_invariant false` for testing |
| 429/503 from TCGplayer CDN during catalog build | Rare; we observed zero in production builds | Drop `--rate` and `--concurrency` in `download-images`; back-rejector pauses 5s automatically |
| OOM during boot | Insufficient container memory | Bump RAM limit to 2 GB |

---

## When NOT to use this runbook

- For website / consumer-side issues. The website owns batches, corrections, image lifecycle — see `docs/API.md` "Website Responsibilities."
- For embedder accuracy issues. Those go through the engine fork (`ipkstef/CollectorVision`), not this repo.
- For TCGplayer parquet content issues (e.g. broken `oracle_text_plain`). Those are with the parquet maintainer / upstream of R2.
