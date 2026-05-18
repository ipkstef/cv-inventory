import csv
import io

import pytest
from fastapi.testclient import TestClient

from scan_and_identify.server.app import create_app
from scan_and_identify.server.state import AppState
from scan_and_identify.tcgplayer.seller_csv import (
    MergePriceConflict,
    build_seller_csv,
    merge_duplicate_rows,
)
from scan_and_identify.tcgplayer.store import TCGStore


def _client(synthetic_catalog, synthetic_parquets):
    state = AppState.bootstrap_for_tests("k", synthetic_catalog, synthetic_parquets)
    return TestClient(create_app(state))


def _rows_from_csv(body: bytes) -> list[dict]:
    return list(csv.DictReader(io.StringIO(body.decode("utf-8"))))


# ---------------------------------------------------------------------------
# Unit-level: merge_duplicate_rows
# ---------------------------------------------------------------------------


def test_merge_sums_quantities_for_identical_skus():
    merged = merge_duplicate_rows(
        [
            {
                "product_id": 1001,
                "printing": "Normal",
                "condition": "Near Mint",
                "language": "English",
                "quantity": 2,
                "marketplace_price": 1.49,
            },
            {
                "product_id": 1001,
                "printing": "Normal",
                "condition": "Near Mint",
                "language": "English",
                "quantity": 3,
                "marketplace_price": 1.49,
            },
        ]
    )
    assert len(merged) == 1
    assert merged[0]["quantity"] == 5
    assert merged[0]["marketplace_price"] == 1.49


def test_merge_keeps_different_skus_separate():
    merged = merge_duplicate_rows(
        [
            {
                "product_id": 1001,
                "printing": "Normal",
                "condition": "Near Mint",
                "language": "English",
                "quantity": 1,
                "marketplace_price": 1.49,
            },
            {
                "product_id": 1001,
                "printing": "Foil",
                "condition": "Near Mint",
                "language": "English",
                "quantity": 1,
                "marketplace_price": 2.99,
            },
        ]
    )
    assert len(merged) == 2


def test_merge_treats_none_price_as_compatible_with_explicit():
    merged = merge_duplicate_rows(
        [
            {
                "product_id": 1001,
                "printing": "Normal",
                "condition": "Near Mint",
                "language": "English",
                "quantity": 1,
                "marketplace_price": None,
            },
            {
                "product_id": 1001,
                "printing": "Normal",
                "condition": "Near Mint",
                "language": "English",
                "quantity": 1,
                "marketplace_price": 1.49,
            },
        ]
    )
    assert len(merged) == 1
    assert merged[0]["quantity"] == 2
    assert merged[0]["marketplace_price"] == 1.49


def test_merge_raises_price_conflict_with_details():
    with pytest.raises(MergePriceConflict) as exc_info:
        merge_duplicate_rows(
            [
                {
                    "product_id": 1001,
                    "printing": "Normal",
                    "condition": "Near Mint",
                    "language": "English",
                    "quantity": 1,
                    "marketplace_price": 1.49,
                },
                {
                    "product_id": 1001,
                    "printing": "Normal",
                    "condition": "Near Mint",
                    "language": "English",
                    "quantity": 1,
                    "marketplace_price": 1.99,
                },
            ]
        )
    assert len(exc_info.value.conflicts) == 1
    c = exc_info.value.conflicts[0]
    assert c["sku_key"]["product_id"] == 1001
    assert c["prices_seen"] == [1.49, 1.99]


# ---------------------------------------------------------------------------
# build_seller_csv with merge_duplicates flag
# ---------------------------------------------------------------------------


def test_csv_merges_by_default(synthetic_parquets):
    store = TCGStore.load(synthetic_parquets)
    body = build_seller_csv(
        store,
        [
            {
                "product_id": 1001,
                "printing": "Normal",
                "condition": "Near Mint",
                "language": "English",
                "quantity": 2,
                "marketplace_price": 0.99,
            },
            {
                "product_id": 1001,
                "printing": "Normal",
                "condition": "Near Mint",
                "language": "English",
                "quantity": 3,
                "marketplace_price": 0.99,
            },
        ],
    )
    rows = _rows_from_csv(body)
    assert len(rows) == 1
    assert rows[0]["Total Quantity"] == "5"


def test_csv_does_not_merge_when_flag_false(synthetic_parquets):
    store = TCGStore.load(synthetic_parquets)
    body = build_seller_csv(
        store,
        [
            {
                "product_id": 1001,
                "printing": "Normal",
                "condition": "Near Mint",
                "language": "English",
                "quantity": 2,
                "marketplace_price": 0.99,
            },
            {
                "product_id": 1001,
                "printing": "Normal",
                "condition": "Near Mint",
                "language": "English",
                "quantity": 3,
                "marketplace_price": 0.99,
            },
        ],
        merge_duplicates=False,
    )
    rows = _rows_from_csv(body)
    assert len(rows) == 2


# ---------------------------------------------------------------------------
# HTTP endpoint
# ---------------------------------------------------------------------------


def test_export_merges_by_default(synthetic_catalog, synthetic_parquets):
    client = _client(synthetic_catalog, synthetic_parquets)
    r = client.post(
        "/export/tcgplayer-csv",
        json={
            "rows": [
                {
                    "product_id": 1001,
                    "printing": "Normal",
                    "condition": "Near Mint",
                    "language": "English",
                    "quantity": 2,
                    "marketplace_price": 1.49,
                },
                {
                    "product_id": 1001,
                    "printing": "Normal",
                    "condition": "Near Mint",
                    "language": "English",
                    "quantity": 3,
                    "marketplace_price": 1.49,
                },
            ]
        },
        headers={"Authorization": "Bearer k"},
    )
    assert r.status_code == 200
    rows = _rows_from_csv(r.content)
    assert len(rows) == 1
    assert rows[0]["Total Quantity"] == "5"


def test_export_merge_false_preserves_rows(synthetic_catalog, synthetic_parquets):
    client = _client(synthetic_catalog, synthetic_parquets)
    r = client.post(
        "/export/tcgplayer-csv",
        json={
            "merge_duplicates": False,
            "rows": [
                {
                    "product_id": 1001,
                    "printing": "Normal",
                    "condition": "Near Mint",
                    "language": "English",
                    "quantity": 1,
                },
                {
                    "product_id": 1001,
                    "printing": "Normal",
                    "condition": "Near Mint",
                    "language": "English",
                    "quantity": 1,
                },
            ],
        },
        headers={"Authorization": "Bearer k"},
    )
    assert r.status_code == 200
    rows = _rows_from_csv(r.content)
    assert len(rows) == 2


def test_export_price_conflict_returns_400(synthetic_catalog, synthetic_parquets):
    client = _client(synthetic_catalog, synthetic_parquets)
    r = client.post(
        "/export/tcgplayer-csv",
        json={
            "rows": [
                {
                    "product_id": 1001,
                    "printing": "Normal",
                    "condition": "Near Mint",
                    "language": "English",
                    "quantity": 1,
                    "marketplace_price": 1.49,
                },
                {
                    "product_id": 1001,
                    "printing": "Normal",
                    "condition": "Near Mint",
                    "language": "English",
                    "quantity": 1,
                    "marketplace_price": 1.99,
                },
            ]
        },
        headers={"Authorization": "Bearer k"},
    )
    assert r.status_code == 400
    body = r.json()
    assert body["error"]["code"] == "merge_price_conflict"
    assert len(body["error"]["conflicts"]) == 1
    c = body["error"]["conflicts"][0]
    assert c["sku_key"]["product_id"] == 1001
    assert c["sku_key"]["printing"] == "Normal"
    assert c["prices_seen"] == [1.49, 1.99]
