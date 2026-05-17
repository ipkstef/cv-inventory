#!/usr/bin/env bash
# Boots the cv-inventory server locally and hits each endpoint.
# Requires: docker, jq, a real catalog at $CV_INVENTORY_CATALOG_PATH.
# Usage:    scripts/smoke_test.sh https://your-storage/sample.jpg

set -euo pipefail

API_KEY="${CV_INVENTORY_API_KEY:-smoketest}"
PORT="${PORT:-8765}"
CATALOG="${CV_INVENTORY_CATALOG_PATH:?Set CV_INVENTORY_CATALOG_PATH first}"
TEST_IMAGE_URL="${1:?Pass an image URL as the first arg}"

cleanup() { docker stop cv-inv-smoke >/dev/null 2>&1 || true; }
trap cleanup EXIT

docker run -d --rm --name cv-inv-smoke \
    -p "$PORT:8000" \
    -e CV_INVENTORY_API_KEY="$API_KEY" \
    -e CV_INVENTORY_CATALOG_PATH="$CATALOG" \
    -e R2_TCGPLAYER_BUCKET_ACCESS_KEY="${R2_TCGPLAYER_BUCKET_ACCESS_KEY}" \
    -e R2_TCGPLAYER_BUCKET_SECRET_KEY="${R2_TCGPLAYER_BUCKET_SECRET_KEY}" \
    -e R2_TCGPLAYER_URL="${R2_TCGPLAYER_URL}" \
    -v "$(dirname "$CATALOG"):$(dirname "$CATALOG")" \
    cv-inventory:test

for _ in $(seq 1 30); do
    sleep 1
    if curl -fsS -H "Authorization: Bearer $API_KEY" "http://localhost:$PORT/health" >/dev/null 2>&1; then
        break
    fi
done

echo "== /health =="
curl -fsS -H "Authorization: Bearer $API_KEY" "http://localhost:$PORT/health" | jq

echo "== /sets (first 3) =="
curl -fsS -H "Authorization: Bearer $API_KEY" "http://localhost:$PORT/sets" | jq '.sets[:3]'

echo "== /identify =="
curl -fsS -X POST -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" \
    -d "{\"image_url\":\"$TEST_IMAGE_URL\",\"top_k\":3}" \
    "http://localhost:$PORT/identify" | jq
