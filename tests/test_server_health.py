from fastapi.testclient import TestClient

from scan_and_identify.server.app import create_app
from scan_and_identify.server.state import AppState


def _client(synthetic_catalog, synthetic_parquets):
    state = AppState.bootstrap_for_tests(
        api_key="testkey",
        catalog_path=synthetic_catalog,
        parquet_dir=synthetic_parquets,
    )
    app = create_app(state)
    return TestClient(app)


def test_health_requires_auth(synthetic_catalog, synthetic_parquets):
    client = _client(synthetic_catalog, synthetic_parquets)
    r = client.get("/health")
    assert r.status_code == 401


def test_health_returns_status(synthetic_catalog, synthetic_parquets):
    client = _client(synthetic_catalog, synthetic_parquets)
    r = client.get("/health", headers={"Authorization": "Bearer testkey"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["catalog_size"] == 4
    assert "catalog_version" in body
