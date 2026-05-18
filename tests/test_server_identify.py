import io

import respx
from fastapi.testclient import TestClient
from httpx import Response
from PIL import Image

from cv_inventory.server.app import create_app
from cv_inventory.server.state import AppState


def _png_bytes() -> bytes:
    img = Image.new("RGB", (448, 448), (200, 50, 50))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_identify_returns_candidates(synthetic_catalog, synthetic_parquets):
    state = AppState.bootstrap_for_tests("k", synthetic_catalog, synthetic_parquets)
    client = TestClient(create_app(state))
    url = "https://example.com/scan.png"
    with respx.mock(assert_all_called=True) as m:
        m.get(url).mock(return_value=Response(200, content=_png_bytes()))
        r = client.post(
            "/identify",
            json={"image_url": url, "top_k": 3, "rotation_invariant": False},
            headers={"Authorization": "Bearer k"},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["is_card_back"] is False
    assert body["confidence"] in {"good", "fair", "poor"}
    assert len(body["candidates"]) == 3
    assert "product_id" in body["candidates"][0]


def test_identify_with_set_lock(synthetic_catalog, synthetic_parquets):
    state = AppState.bootstrap_for_tests("k", synthetic_catalog, synthetic_parquets)
    client = TestClient(create_app(state))
    url = "https://example.com/scan.png"
    with respx.mock(assert_all_called=True) as m:
        m.get(url).mock(return_value=Response(200, content=_png_bytes()))
        r = client.post(
            "/identify",
            json={"image_url": url, "set_id": 100, "top_k": 5, "rotation_invariant": False},
            headers={"Authorization": "Bearer k"},
        )
    assert r.status_code == 200
    assert all(c["group_id"] == 100 for c in r.json()["candidates"])


def test_identify_image_fetch_404(synthetic_catalog, synthetic_parquets):
    state = AppState.bootstrap_for_tests("k", synthetic_catalog, synthetic_parquets)
    client = TestClient(create_app(state))
    url = "https://example.com/missing.png"
    with respx.mock() as m:
        m.get(url).mock(return_value=Response(404))
        r = client.post(
            "/identify",
            json={"image_url": url},
            headers={"Authorization": "Bearer k"},
        )
    assert r.status_code == 400
    assert "fetch" in r.json()["error"]["message"].lower()
