import io
import json

import numpy as np
import pandas as pd
import respx
from httpx import Response
from PIL import Image

from cv_inventory.catalog_build import build_catalog


def _png(color):
    buf = io.BytesIO()
    Image.new("RGB", (300, 300), color).save(buf, format="JPEG")
    return buf.getvalue()


def test_build_catalog_filters_sealed_and_writes_npz(tmp_path):
    products_path = tmp_path / "products.parquet"
    pd.DataFrame(
        {
            "product_id": [1001, 1002, 9999],
            "group_id": [100, 100, 100],
            "name": ["A", "B", "Sealed"],
            "image_url": [
                f"https://tcgplayer-cdn.tcgplayer.com/product/{p}_200w.jpg"
                for p in [1001, 1002, 9999]
            ],
            "is_sealed": [False, False, True],
        }
    ).to_parquet(products_path)

    out = tmp_path / "catalog.npz"
    cache = tmp_path / "imgs"

    with respx.mock(assert_all_called=False) as m:
        m.get(url__regex=r".*_in_1000x1000\.jpg").mock(return_value=Response(404))
        m.get(url__regex=r".*_200w\.jpg").mock(
            return_value=Response(
                200,
                content=_png((128, 128, 128)),
                headers={"content-type": "image/jpeg"},
            )
        )
        build_catalog(products_parquet=products_path, out_path=out, image_cache=cache)

    data = np.load(out, allow_pickle=False)
    assert data["embeddings"].shape == (2, 128)
    assert sorted(data["card_ids"].tolist()) == ["1001", "1002"]
    spec = json.loads(str(data["embedder_spec"]))
    assert spec["kind"] == "neural"
    assert spec["algo_key"] == "milo1"
