from fastapi.testclient import TestClient

from cv_inventory.server.app import create_app
from cv_inventory.server.state import AppState


def _client(synthetic_catalog, synthetic_parquets):
    state = AppState.bootstrap_for_tests("k", synthetic_catalog, synthetic_parquets)
    return TestClient(create_app(state))


def test_get_product_returns_metadata_and_skus(synthetic_catalog, synthetic_parquets):
    client = _client(synthetic_catalog, synthetic_parquets)
    r = client.get("/products/1001", headers={"Authorization": "Bearer k"})
    assert r.status_code == 200
    body = r.json()
    assert body["product_id"] == 1001
    assert body["name"] == "Alpha Card 1"
    assert body["set_abbr"] == "TSA"
    assert len(body["skus"]) == 4


def test_get_product_404_when_unknown(synthetic_catalog, synthetic_parquets):
    client = _client(synthetic_catalog, synthetic_parquets)
    r = client.get("/products/777777", headers={"Authorization": "Bearer k"})
    assert r.status_code == 404


def test_resolve_sku_returns_match(synthetic_catalog, synthetic_parquets):
    client = _client(synthetic_catalog, synthetic_parquets)
    r = client.post(
        "/products/1001/resolve-sku",
        json={"printing": "Normal", "condition": "Near Mint", "language": "English"},
        headers={"Authorization": "Bearer k"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["sku_id"] > 0


def test_resolve_sku_404_when_combination_missing(synthetic_catalog, synthetic_parquets):
    client = _client(synthetic_catalog, synthetic_parquets)
    r = client.post(
        "/products/1001/resolve-sku",
        json={"printing": "Normal", "condition": "Damaged", "language": "English"},
        headers={"Authorization": "Bearer k"},
    )
    assert r.status_code == 404
