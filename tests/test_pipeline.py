from PIL import Image

from collector_vision import Catalog, NeuralEmbedder
from cv_inventory.back_rejector import BackRejector
from cv_inventory.pipeline import IdentifyPipeline
from cv_inventory.set_index import SetIndex
from cv_inventory.tcgplayer.store import TCGStore


def _make_pipeline(catalog_path, parquets_path):
    catalog = Catalog.load(catalog_path)
    store = TCGStore.load(parquets_path)
    index = SetIndex.build(catalog, store)
    embedder = NeuralEmbedder()
    return IdentifyPipeline(
        embedder=embedder,
        index=index,
        store=store,
        back_rejector=BackRejector(back_embedding=None),
    )


def test_pipeline_returns_candidates_with_metadata(synthetic_catalog, synthetic_parquets):
    pipeline = _make_pipeline(synthetic_catalog, synthetic_parquets)
    img = Image.new("RGB", (448, 448), (200, 50, 50))
    result = pipeline.identify(img, set_id=None, top_k=3, rotation_invariant=False)
    assert result.is_card_back is False
    assert len(result.candidates) == 3
    top = result.candidates[0]
    assert top.product_id in {1001, 1002, 1003, 2001}
    assert top.name.startswith(("Alpha", "Beta"))
    assert 0.0 <= top.score <= 1.0


def test_pipeline_set_lock_restricts_results(synthetic_catalog, synthetic_parquets):
    pipeline = _make_pipeline(synthetic_catalog, synthetic_parquets)
    img = Image.new("RGB", (448, 448), (200, 50, 50))
    result = pipeline.identify(img, set_id=100, top_k=5, rotation_invariant=False)
    assert all(c.group_id == 100 for c in result.candidates)


def test_pipeline_rotation_invariant_does_not_break(synthetic_catalog, synthetic_parquets):
    pipeline = _make_pipeline(synthetic_catalog, synthetic_parquets)
    img = Image.new("RGB", (448, 448), (200, 50, 50))
    result = pipeline.identify(img, set_id=None, top_k=3, rotation_invariant=True)
    assert len(result.candidates) == 3
