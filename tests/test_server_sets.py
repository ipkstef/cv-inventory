from fastapi.testclient import TestClient

from cv_inventory.server.app import create_app
from cv_inventory.server.state import AppState


def test_sets_returns_current_first(synthetic_catalog, synthetic_parquets):
    state = AppState.bootstrap_for_tests("k", synthetic_catalog, synthetic_parquets)
    client = TestClient(create_app(state))
    r = client.get("/sets", headers={"Authorization": "Bearer k"})
    assert r.status_code == 200
    sets = r.json()["sets"]
    assert sets[0]["abbr"] == "TSA"
    assert sets[0]["is_current"] is True
