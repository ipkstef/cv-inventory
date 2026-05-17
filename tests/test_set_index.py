import pytest

from collector_vision import Catalog
from cv_inventory.set_index import SetIndex
from cv_inventory.tcgplayer.store import TCGStore


def test_set_index_filters_to_one_set(synthetic_catalog, synthetic_parquets):
    catalog = Catalog.load(synthetic_catalog)
    store = TCGStore.load(synthetic_parquets)

    idx = SetIndex.build(catalog, store)

    full = idx.search(catalog.embeddings[0], set_id=None, top_k=5)
    assert {pid for _, pid in full} == {1001, 1002, 1003, 2001}

    only_tsa = idx.search(catalog.embeddings[0], set_id=100, top_k=5)
    assert {pid for _, pid in only_tsa} == {1001, 1002, 1003}


def test_set_index_unknown_set_raises(synthetic_catalog, synthetic_parquets):
    catalog = Catalog.load(synthetic_catalog)
    store = TCGStore.load(synthetic_parquets)
    idx = SetIndex.build(catalog, store)
    with pytest.raises(KeyError):
        idx.search(catalog.embeddings[0], set_id=99999, top_k=5)
