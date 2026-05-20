# scan-and-identify Operations Runbook

Everything you need to deploy, refresh, and observe the running system.

---

## Environment variables

| Var | Required | Default | Notes |
|---|---|---|---|
| `SCAN_AND_IDENTIFY_API_KEY` | yes | — | Shared bearer token. Generate via e.g. `openssl rand -base64 48`. |
| `SCAN_AND_IDENTIFY_CATALOG_PATH` | yes | — | Absolute path to the `.npz` catalog. In Docker, baked in at `/app/catalogs/<file>.npz`. |
| `R2_TCGPLAYER_BUCKET_ACCESS_KEY` | yes | — | R2 access key for the `tcgplayerapi` bucket. |
| `R2_TCGPLAYER_BUCKET_SECRET_KEY` | yes | — | R2 secret key. |
| `R2_TCGPLAYER_URL` | yes | — | R2 endpoint, e.g. `https://<account-id>.r2.cloudflarestorage.com`. |
| `SCAN_AND_IDENTIFY_TCGPLAYER_CATEGORY` | no | `1` | TCGplayer category id. `1` = MTG. |
| `SCAN_AND_IDENTIFY_CONF_GOOD_SCORE` | no | `0.55` | Confidence tier threshold (top-1 score). |
| `SCAN_AND_IDENTIFY_CONF_GOOD_GAP` | no | `0.15` | Confidence tier threshold (gap to top-2). |
| `SCAN_AND_IDENTIFY_CONF_POOR_SCORE` | no | `0.45` | Below this score → `poor`. |
| `SCAN_AND_IDENTIFY_CONF_POOR_GAP` | no | `0.05` | Below this gap → `poor`. |
| `AWS_DEFAULT_REGION` | no | `auto` | Set by boto3 for R2; usually not needed. |

The container will fail fast at boot with `ConfigError: Missing required environment variables: ...` if any required var is unset. Wire that into your alerting.

---

## Deployment

Images are built by the **`Build image` GitHub Actions workflow** (manual
trigger) and published to `ghcr.io/ipkstef/scan-and-identify` with two
tags per build:

- `ghcr.io/ipkstef/scan-and-identify:<short-sha>` — pinned, for rollback
- `ghcr.io/ipkstef/scan-and-identify:latest` — convenience

On any production host:

```bash
# One-time setup
sudo apt update && sudo apt install -y docker.io docker-compose-plugin
mkdir scan-and-identify && cd scan-and-identify
curl -fsSLO https://raw.githubusercontent.com/ipkstef/scan-and-identify/main/docker-compose.yml
curl -fsSLO https://raw.githubusercontent.com/ipkstef/scan-and-identify/main/.env.example
cp .env.example .env && nano .env   # fill in secrets

# Deploy or upgrade
docker compose pull && docker compose up -d
```

That's the whole production deploy. No git clone, no LFS, no Docker build on the host.

Rollback to a specific image SHA: edit `docker-compose.yml` to pin
`ghcr.io/ipkstef/scan-and-identify:<sha>` and `docker compose up -d`.

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

TCGplayer ships new products roughly monthly; refresh the catalog so they're
identifiable. The whole refresh is one script + one button click + one
`docker compose pull`.

### 1. Generate a new NPZ

On any machine with Docker, the repo cloned (with LFS), and a populated
`.env`:

```bash
cd scan-and-identify
./scripts/refresh-catalog.sh
```

The script runs entirely inside the existing Docker image — no Python venv,
no AWS CLI, no host toolchain. It:

1. Pulls the latest `products.parquet` from R2.
2. Downloads any new card images (existing cache reused — typical month is a
   few hundred new products on top of ~110k cached).
3. Re-embeds the full catalog **and computes name-region pHashes** (~75 min CPU; the slow step).
4. Writes the result to `catalogs/catalog.npz` and bakes a `built_at`
   timestamp into the NPZ.

Image cache lives at `~/scan-and-identify-cache/` by default; override with
`IMAGE_CACHE=/path/to/cache`. Other knobs: `RATE`, `CONCURRENCY`, `IMAGE_TAG`.

### 2. Commit + trigger CI

```bash
git add catalogs/catalog.npz
git commit -m "Refresh catalog $(date +%Y-%m)"
git push
```

Then in GitHub: **Actions → Build image → Run workflow**. CI builds a new
image tagged with the SHA + `:latest` and pushes to GHCR. ~3-5 min.

### 3. Deploy

On the production host:

```bash
docker compose pull && docker compose up -d
```

Container restarts with the new image and the new catalog. ~10s downtime.

### Catalog versioning

The NPZ embeds an `algo_key` field. Current servers require `milo1+phash1`
(Milo embedder + 64-bit name-region pHash). Older `milo1` catalogs are
**rejected at boot** with a clear `ValueError` — there is no silent
fallback to embedding-only. If you roll back the server image to a
pre-pHash version, you must also roll the catalog back to a `milo1`-tagged
NPZ. The image and the catalog move together.

### Performance tuning

- The download phase is rate-limited. We observed clean 40 req/s on
  Cloudflare-fronted TCGplayer CDN with zero throttling — push higher
  cautiously (`RATE=60`) and monitor for 429/503.
- The embed phase is CPU-bound and serial because Milo's ONNX is exported
  with `batch_size=1`. ~25 products/sec is the ceiling without an
  engine-side fix.

### Resume if the build dies

The image cache persists between runs; re-running the refresh script skips
anything already downloaded. The embed pass re-embeds everything (we don't
incrementally update the NPZ), but downloads are the slow part — embed-only
after a crash is ~75 min vs ~90 min for the full pipeline.

---

## Nightly restart for fresh prices

Parquets are a **boot-time snapshot** — once loaded into the in-memory
`TCGStore`, they don't auto-refresh. TCGplayer publishes new prices roughly
daily, so a container that runs for two weeks serves two-week-old prices.

Cheapest fix: restart the container every night. R2 sync at boot detects
the updated parquets and reloads them. Downtime per restart is ~5-10
seconds (catalog + parquet reload).

Pick a quiet hour (3am local is fine for most use cases):

```bash
# On the host running the container:
sudo crontab -e
# Add:
0 3 * * * docker restart scan-and-identify >/dev/null 2>&1
```

Or as a systemd timer if you prefer. If your website handles brief 5xx
gracefully (it should — networks blip), this is invisible to users.

If you ever need fresher than daily, two next steps would be:
- Add a `POST /admin/refresh` endpoint that re-syncs parquets without a restart.
- Background asyncio task inside the container that periodically swaps the
  TCGStore atomically.

Neither is built yet. Restart-on-cron is the v1 answer.

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
docker logs -f scan-and-identify
docker logs scan-and-identify 2>&1 | grep -iE "error|warning"
```

In production you'd ship these to your log aggregator of choice. There's nothing structured-logging-aware in scan-and-identify today; logs are plain text.

---

## Rotating the API key

Generate a new key, update the website's secret, then redeploy with `SCAN_AND_IDENTIFY_API_KEY=<newkey>`:

```bash
NEWKEY=$(openssl rand -base64 48 | tr -d '=+/' | head -c 48)
echo "SCAN_AND_IDENTIFY_API_KEY=$NEWKEY"
# Update website secret, then:
docker stop scan-and-identify && docker rm scan-and-identify
docker run -d --name scan-and-identify --restart=unless-stopped -p 8000:8000 \
  --env-file /etc/scan-and-identify/.env  scan-and-identify:latest
```

No graceful rotation supported in v1 (single shared key). If you need zero-downtime rotation, you'd want per-key auth — not implemented.

---

## Common failures and what they mean

| Symptom | Cause | Fix |
|---|---|---|
| Container exits on boot with `ConfigError` | Missing env var | Check `.env`; see required vars table above |
| Container exits with `FileNotFoundError: catalog.npz` | `SCAN_AND_IDENTIFY_CATALOG_PATH` points at a non-existent file | Verify path; rebuild image if catalog was supposed to be baked in |
| Boot hangs >30s | R2 sync is timing out | Check `R2_TCGPLAYER_URL`, network reachability, credentials |
| All `/identify` requests return 400 "Could not fetch image" | Consumer storage is unreachable from the container | Network/firewall; pre-signed URLs may have expired |
| `/identify` returns wrong card consistently for one set | Catalog is stale for that set | Refresh catalog (see above) |
| Slow `/identify` (>2s) | Image fetch from consumer storage is slow | Investigate storage latency; pre-warm with `--rotation_invariant false` for testing |
| 429/503 from TCGplayer CDN during catalog build | Rare; we observed zero in production builds | Drop `--rate` and `--concurrency` in `download-images`; back-rejector pauses 5s automatically |
| OOM during boot | Insufficient container memory | Bump RAM limit to 2 GB |
| Container exits with `ValueError: ... requires 'milo1+phash1'` | Loaded catalog was built before pHash rerank was added | Rebuild with `scripts/refresh-catalog.sh` and redeploy; intentional — no silent fallback |

---

## When NOT to use this runbook

- For website / consumer-side issues. The website owns batches, corrections, image lifecycle — see `docs/API.md` "Website Responsibilities."
- For embedder accuracy issues. Those go through the engine fork (`ipkstef/CollectorVision`), not this repo.
- For TCGplayer parquet content issues (e.g. broken `oracle_text_plain`). Those are with the parquet maintainer / upstream of R2.
