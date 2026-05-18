import io

import respx
from fastapi.testclient import TestClient
from httpx import Response
from PIL import Image

from scan_and_identify.server.app import create_app
from scan_and_identify.server.state import AppState


def _png_bytes(color):
    img = Image.new("RGB", (448, 448), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_identify_batch_returns_one_result_per_image(synthetic_catalog, synthetic_parquets):
    state = AppState.bootstrap_for_tests("k", synthetic_catalog, synthetic_parquets)
    client = TestClient(create_app(state))
    with respx.mock(assert_all_called=True) as m:
        m.get("https://e.com/a.png").mock(
            return_value=Response(200, content=_png_bytes((255, 0, 0)))
        )
        m.get("https://e.com/b.png").mock(
            return_value=Response(200, content=_png_bytes((0, 255, 0)))
        )
        m.get("https://e.com/c.png").mock(return_value=Response(404))
        r = client.post(
            "/identify-batch",
            json={
                "images": [
                    {"id": "a", "image_url": "https://e.com/a.png"},
                    {"id": "b", "image_url": "https://e.com/b.png"},
                    {"id": "c", "image_url": "https://e.com/c.png"},
                ],
                "top_k": 2,
                "rotation_invariant": False,
            },
            headers={"Authorization": "Bearer k"},
        )
    assert r.status_code == 200, r.text
    results = {item["id"]: item for item in r.json()["results"]}
    assert results["a"]["error"] is None
    assert results["b"]["error"] is None
    assert results["c"]["error"] is not None and "fetch" in results["c"]["error"].lower()
    assert len(results["a"]["candidates"]) == 2
