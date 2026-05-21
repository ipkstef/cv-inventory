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


def test_identify_batch_thread_safety_under_concurrency(synthetic_catalog, synthetic_parquets):
    """Run a 20-image batch and verify every result is well-formed.

    Exercises the asyncio.to_thread path with enough items to actually queue
    through the default thread pool (~12 workers). If pipeline.identify or
    any of its dependencies (embedder, set_index, store, back_rejector) had
    a thread-safety bug, we'd see corrupted candidates or exceptions.
    """
    import io as _io
    from PIL import Image as _Image

    state = AppState.bootstrap_for_tests("k", synthetic_catalog, synthetic_parquets)
    client = TestClient(create_app(state))

    n = 20
    images = []
    with respx.mock(assert_all_called=False) as m:
        for i in range(n):
            url = f"https://e.com/scan-{i:02d}.png"
            # Vary colors per scan so embeddings differ slightly batch-to-batch
            color = ((i * 13) % 256, (i * 47) % 256, (i * 91) % 256)
            buf = _io.BytesIO()
            _Image.new("RGB", (448, 448), color).save(buf, format="PNG")
            m.get(url).mock(return_value=Response(200, content=buf.getvalue()))
            images.append({"id": f"s{i:02d}", "image_url": url})
        r = client.post(
            "/identify-batch",
            json={"images": images, "top_k": 3, "rotation_invariant": False},
            headers={"Authorization": "Bearer k"},
        )

    assert r.status_code == 200, r.text
    results = r.json()["results"]
    assert len(results) == n
    # Every result must be well-formed (no exceptions slipped through)
    for item in results:
        assert item["error"] is None
        assert isinstance(item["candidates"], list)
        assert len(item["candidates"]) == 3
        # Each candidate must have all the expected fields populated
        for c in item["candidates"]:
            assert c["product_id"] > 0
            assert c["name"]
            assert isinstance(c["printings"], list)
            assert 0.0 <= c["score"] <= 1.0
    # Response IDs are returned in the same order as requested
    assert [item["id"] for item in results] == [f"s{i:02d}" for i in range(n)]
