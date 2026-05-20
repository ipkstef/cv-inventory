from fastapi.testclient import TestClient

from scan_and_identify.server.app import create_app
from scan_and_identify.server.state import AppState
from scan_and_identify.tcgplayer.store import TCGStore


def _client(synthetic_catalog, synthetic_parquets):
    state = AppState.bootstrap_for_tests("k", synthetic_catalog, synthetic_parquets)
    return TestClient(create_app(state))


def test_store_search_by_name_substring_case_insensitive(synthetic_parquets):
    store = TCGStore.load(synthetic_parquets)
    results = store.search_products(name="alpha")
    pids = {r["product_id"] for r in results}
    # All Alpha Card 1/2/3 — sealed "Alpha Sealed Box" excluded.
    assert pids == {1001, 1002, 1003}


def test_store_search_by_collector_number_exact(synthetic_parquets):
    store = TCGStore.load(synthetic_parquets)
    results = store.search_products(collector_number="2")
    pids = {r["product_id"] for r in results}
    assert pids == {1002}


def test_store_search_with_single_set_filter(synthetic_parquets):
    store = TCGStore.load(synthetic_parquets)
    results = store.search_products(name="card", set_ids=[200])
    pids = {r["product_id"] for r in results}
    assert pids == {2001}  # Beta Card 1, the only product in set 200


def test_store_search_with_union_set_filter(synthetic_parquets):
    store = TCGStore.load(synthetic_parquets)
    results = store.search_products(name="card", set_ids=[100, 200])
    pids = {r["product_id"] for r in results}
    assert pids == {1001, 1002, 1003, 2001}


def test_store_search_rejects_empty_set_ids(synthetic_parquets):
    import pytest

    store = TCGStore.load(synthetic_parquets)
    with pytest.raises(ValueError, match="non-empty"):
        store.search_products(name="card", set_ids=[])


def test_store_search_rejects_unknown_set_id(synthetic_parquets):
    import pytest

    store = TCGStore.load(synthetic_parquets)
    with pytest.raises(KeyError, match="99999"):
        store.search_products(name="card", set_ids=[100, 99999])


def test_store_search_empty_when_no_filters(synthetic_parquets):
    store = TCGStore.load(synthetic_parquets)
    assert store.search_products() == []


def test_search_endpoint_requires_name_or_collector_number(synthetic_catalog, synthetic_parquets):
    client = _client(synthetic_catalog, synthetic_parquets)
    r = client.get("/search", headers={"Authorization": "Bearer k"})
    assert r.status_code == 400
    assert "name" in r.json()["error"]["message"].lower()


def test_search_endpoint_returns_matches(synthetic_catalog, synthetic_parquets):
    client = _client(synthetic_catalog, synthetic_parquets)
    r = client.get(
        "/search",
        params={"name": "alpha"},
        headers={"Authorization": "Bearer k"},
    )
    assert r.status_code == 200
    results = r.json()["results"]
    assert {x["product_id"] for x in results} == {1001, 1002, 1003}
    # ProductMatch shape — no score field
    assert "score" not in results[0]
    assert "product_id" in results[0]
    assert "name" in results[0]


def test_search_endpoint_single_set_filter(synthetic_catalog, synthetic_parquets):
    client = _client(synthetic_catalog, synthetic_parquets)
    r = client.get(
        "/search",
        params={"name": "card", "set_ids": 100},
        headers={"Authorization": "Bearer k"},
    )
    assert r.status_code == 200
    assert {x["group_id"] for x in r.json()["results"]} == {100}


def test_search_endpoint_union_set_filter(synthetic_catalog, synthetic_parquets):
    client = _client(synthetic_catalog, synthetic_parquets)
    # Repeated query param for list: ?set_ids=100&set_ids=200
    r = client.get(
        "/search",
        params=[("name", "card"), ("set_ids", 100), ("set_ids", 200)],
        headers={"Authorization": "Bearer k"},
    )
    assert r.status_code == 200
    assert {x["group_id"] for x in r.json()["results"]} <= {100, 200}


def test_search_endpoint_unknown_set_id_is_404(synthetic_catalog, synthetic_parquets):
    client = _client(synthetic_catalog, synthetic_parquets)
    r = client.get(
        "/search",
        params=[("name", "card"), ("set_ids", 100), ("set_ids", 99999)],
        headers={"Authorization": "Bearer k"},
    )
    assert r.status_code == 404
    assert "99999" in r.json()["error"]["message"]


def test_search_endpoint_limit_clamp(synthetic_catalog, synthetic_parquets):
    client = _client(synthetic_catalog, synthetic_parquets)
    r = client.get(
        "/search",
        params={"name": "alpha", "limit": 0},
        headers={"Authorization": "Bearer k"},
    )
    assert r.status_code == 400
    r = client.get(
        "/search",
        params={"name": "alpha", "limit": 101},
        headers={"Authorization": "Bearer k"},
    )
    assert r.status_code == 400


def test_search_endpoint_requires_auth(synthetic_catalog, synthetic_parquets):
    client = _client(synthetic_catalog, synthetic_parquets)
    r = client.get("/search", params={"name": "alpha"})
    assert r.status_code == 401
