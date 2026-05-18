#!/usr/bin/env bash
# Boots the cv-inventory server locally and hits every endpoint.
# Requires: docker, jq, curl, a real catalog at $CV_INVENTORY_CATALOG_PATH.
# Usage:    scripts/smoke_test.sh [scan_image_url]
#
# If no scan_image_url is given, /identify uses a known TCGplayer product image.

set -euo pipefail

API_KEY="${CV_INVENTORY_API_KEY:-smoketest}"
PORT="${PORT:-8765}"
CATALOG="${CV_INVENTORY_CATALOG_PATH:?Set CV_INVENTORY_CATALOG_PATH first}"
TEST_IMAGE_URL="${1:-https://tcgplayer-cdn.tcgplayer.com/product/218276_in_1000x1000.jpg}"
TEST_PRODUCT_ID=218276  # Sword of War and Peace (Borderless) — DSK
TEST_SET_NAME="lightning"  # for /search

BASE="http://localhost:$PORT"
AUTH=(-H "Authorization: Bearer $API_KEY")
JSON=(-H "Content-Type: application/json")

cleanup() { docker stop cv-inv-smoke >/dev/null 2>&1 || true; }
trap cleanup EXIT

echo "==> Booting cv-inv-smoke (catalog: $(basename "$CATALOG"))"
docker run -d --rm --name cv-inv-smoke \
    -p "$PORT:8000" \
    -e CV_INVENTORY_API_KEY="$API_KEY" \
    -e CV_INVENTORY_CATALOG_PATH="$CATALOG" \
    -e R2_TCGPLAYER_BUCKET_ACCESS_KEY="${R2_TCGPLAYER_BUCKET_ACCESS_KEY}" \
    -e R2_TCGPLAYER_BUCKET_SECRET_KEY="${R2_TCGPLAYER_BUCKET_SECRET_KEY}" \
    -e R2_TCGPLAYER_URL="${R2_TCGPLAYER_URL}" \
    -v "$(dirname "$CATALOG"):$(dirname "$CATALOG")" \
    cv-inventory:test >/dev/null

echo "==> Waiting for /health (up to 60s)"
for _ in $(seq 1 60); do
    sleep 1
    if curl -fsS "${AUTH[@]}" "$BASE/health" >/dev/null 2>&1; then
        break
    fi
done

run() {
    # Usage: run "description" curl args...
    local desc="$1"; shift
    echo
    echo "== $desc =="
    if "$@" | jq .; then
        echo "  OK"
    else
        echo "  FAILED"
        exit 1
    fi
}

run "GET /health" \
    curl -fsS "${AUTH[@]}" "$BASE/health"

echo
echo "== auth check (no token) =="
status=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/health")
[ "$status" = "401" ] && echo "  OK (401 as expected)" || { echo "  FAILED: got $status"; exit 1; }

run "GET /sets (first 3)" \
    curl -fsS "${AUTH[@]}" "$BASE/sets?_" \
    | jq '{count: (.sets|length), first: .sets[:3]}'

run "GET /search?name=$TEST_SET_NAME" \
    curl -fsS "${AUTH[@]}" "$BASE/search?name=$TEST_SET_NAME&limit=3"

run "GET /search (missing required param → 400)" \
    bash -c "curl -s '${AUTH[@]}' '$BASE/search' | jq ."
# (above is informational; doesn't fail the script)

run "POST /identify" \
    curl -fsS -X POST "${AUTH[@]}" "${JSON[@]}" \
        -d "{\"image_url\":\"$TEST_IMAGE_URL\",\"top_k\":3,\"rotation_invariant\":false}" \
        "$BASE/identify"

run "POST /identify-batch (2 images)" \
    curl -fsS -X POST "${AUTH[@]}" "${JSON[@]}" \
        -d "{\"images\":[{\"id\":\"a\",\"image_url\":\"$TEST_IMAGE_URL\"},{\"id\":\"b\",\"image_url\":\"$TEST_IMAGE_URL\"}],\"top_k\":3,\"rotation_invariant\":false}" \
        "$BASE/identify-batch"

run "GET /products/$TEST_PRODUCT_ID" \
    curl -fsS "${AUTH[@]}" "$BASE/products/$TEST_PRODUCT_ID" \
    | jq '{product_id, name, set_abbr, rarity, sku_count: (.skus|length)}'

run "POST /products/$TEST_PRODUCT_ID/resolve-sku" \
    curl -fsS -X POST "${AUTH[@]}" "${JSON[@]}" \
        -d '{"printing":"Normal","condition":"Near Mint","language":"English"}' \
        "$BASE/products/$TEST_PRODUCT_ID/resolve-sku"

echo
echo "== POST /export/tcgplayer-csv (one row, with formula) =="
curl -fsS -X POST "${AUTH[@]}" "${JSON[@]}" \
    -d "{\"rows\":[{\"product_id\":$TEST_PRODUCT_ID,\"printing\":\"Normal\",\"condition\":\"Near Mint\",\"language\":\"English\",\"quantity\":1}],\"price_formula\":{\"reference\":\"market\",\"modifier\":{\"type\":\"percent\",\"value\":5.0}}}" \
    "$BASE/export/tcgplayer-csv" \
    | head -2
echo "  OK"

echo
echo "==> Smoke test passed."
