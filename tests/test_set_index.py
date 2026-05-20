import pytest
from collector_vision import Catalog

from scan_and_identify.set_index import SetIndex
from scan_and_identify.tcgplayer.store import TCGStore


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


def test_set_index_exposes_name_phash_for_product(synthetic_catalog, synthetic_parquets):
    import numpy as np

    catalog = Catalog.load(synthetic_catalog)
    store = TCGStore.load(synthetic_parquets)
    pids = [int(cid) for cid in catalog.card_ids]
    phashes = {pid: np.uint64(0xDEAD0000 | i) for i, pid in enumerate(pids)}
    phash_array = np.array([phashes[pid] for pid in pids], dtype=np.uint64)

    index = SetIndex.build(catalog, store, name_phashes=phash_array)
    for pid in pids:
        assert index.name_phash_for(pid) == phashes[pid]


def test_set_index_returns_none_when_phashes_not_provided(synthetic_catalog, synthetic_parquets):
    catalog = Catalog.load(synthetic_catalog)
    store = TCGStore.load(synthetic_parquets)
    index = SetIndex.build(catalog, store)
    assert index.name_phash_for(int(catalog.card_ids[0])) is None


def test_set_index_rejects_mismatched_phash_length(synthetic_catalog, synthetic_parquets):
    import numpy as np

    catalog = Catalog.load(synthetic_catalog)
    store = TCGStore.load(synthetic_parquets)
    wrong = np.array([1, 2], dtype=np.uint64)  # catalog has 4 items
    with pytest.raises(ValueError, match="doesn't match"):
        SetIndex.build(catalog, store, name_phashes=wrong)
