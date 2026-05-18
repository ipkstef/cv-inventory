import csv
import io

from fastapi.testclient import TestClient

from cv_inventory.server.app import create_app
from cv_inventory.server.state import AppState
from cv_inventory.tcgplayer.seller_csv import apply_price_formula


def _client(synthetic_catalog, synthetic_parquets):
    state = AppState.bootstrap_for_tests("k", synthetic_catalog, synthetic_parquets)
    return TestClient(create_app(state))


def _rows_from_csv(body: bytes) -> list[dict]:
    return list(csv.DictReader(io.StringIO(body.decode("utf-8"))))


# ---------------------------------------------------------------------------
# apply_price_formula unit tests
# ---------------------------------------------------------------------------


def test_formula_percent_positive():
    sku = {"market_price": 10.00}
    out = apply_price_formula(
        {"reference": "market", "modifier": {"type": "percent", "value": 5.0}}, sku
    )
    assert out == 10.50


def test_formula_percent_negative():
    sku = {"market_price": 10.00}
    out = apply_price_formula(
        {"reference": "market", "modifier": {"type": "percent", "value": -10.0}}, sku
    )
    assert out == 9.00


def test_formula_fixed():
    sku = {"low_price": 0.50}
    out = apply_price_formula(
        {"reference": "low", "modifier": {"type": "fixed", "value": -0.01}}, sku
    )
    assert out == 0.49


def test_formula_no_modifier_returns_reference():
    sku = {"mid_price": 1.23}
    out = apply_price_formula({"reference": "mid"}, sku)
    assert out == 1.23


def test_formula_null_reference_returns_none():
    sku = {"market_price": None, "low_price": 1.00}
    out = apply_price_formula(
        {"reference": "market", "modifier": {"type": "percent", "value": 5.0}}, sku
    )
    assert out is None


def test_formula_no_sku_returns_none():
    out = apply_price_formula(
        {"reference": "market", "modifier": {"type": "fixed", "value": 1.0}}, None
    )
    assert out is None


def test_formula_no_formula_returns_none():
    assert apply_price_formula(None, {"market_price": 5.0}) is None


# ---------------------------------------------------------------------------
# HTTP endpoint integration
# ---------------------------------------------------------------------------


def test_export_formula_fills_when_no_marketplace_price(synthetic_catalog, synthetic_parquets):
    """No per-row price → formula applies (synthetic SKU has market=2.00)."""
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
                },
            ],
            "price_formula": {
                "reference": "market",
                "modifier": {"type": "percent", "value": 5.0},
            },
        },
        headers={"Authorization": "Bearer k"},
    )
    assert r.status_code == 200
    rows = _rows_from_csv(r.content)
    # Synthetic fixture: market_price_cents = 200 + sku_id % 50 -> $2.00..$2.49 range.
    # Whatever the exact value, formula applied = base * 1.05, not blank.
    assert rows[0]["TCG Marketplace Price"] != ""
    assert float(rows[0]["TCG Marketplace Price"]) > 0


def test_export_per_row_price_wins_over_formula(synthetic_catalog, synthetic_parquets):
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
                    "marketplace_price": 99.99,
                },
            ],
            "price_formula": {
                "reference": "market",
                "modifier": {"type": "percent", "value": 5.0},
            },
        },
        headers={"Authorization": "Bearer k"},
    )
    assert r.status_code == 200
    rows = _rows_from_csv(r.content)
    assert rows[0]["TCG Marketplace Price"] == "99.99"


def test_export_formula_blank_when_unresolvable(synthetic_catalog, synthetic_parquets):
    """Row whose (product,printing,condition,language) doesn't resolve to a SKU
    has no reference price, so the formula yields nothing and the column is blank."""
    client = _client(synthetic_catalog, synthetic_parquets)
    r = client.post(
        "/export/tcgplayer-csv",
        json={
            "rows": [
                {
                    "product_id": 1001,
                    "printing": "Normal",
                    "condition": "Damaged",  # no SKU exists in fixtures
                    "language": "English",
                    "quantity": 1,
                },
            ],
            "price_formula": {
                "reference": "market",
                "modifier": {"type": "percent", "value": 5.0},
            },
        },
        headers={"Authorization": "Bearer k"},
    )
    assert r.status_code == 200
    rows = _rows_from_csv(r.content)
    assert rows[0]["TCG Marketplace Price"] == ""
    assert rows[0]["TCGplayer Id"] == ""  # also blank since SKU didn't resolve
