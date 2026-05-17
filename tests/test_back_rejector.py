import numpy as np

from cv_inventory.back_rejector import BackRejector


def _unit(v: np.ndarray) -> np.ndarray:
    return v / np.linalg.norm(v)


def test_back_rejector_flags_when_back_score_higher():
    back_emb = _unit(np.array([1.0, 0.0, 0.0], dtype=np.float32))
    rejector = BackRejector(back_embedding=back_emb)
    assert rejector.is_back(embedding=back_emb, top_catalog_score=0.7)


def test_back_rejector_does_not_flag_when_top_score_higher():
    back_emb = _unit(np.array([1.0, 0.0, 0.0], dtype=np.float32))
    other = _unit(np.array([0.0, 1.0, 0.0], dtype=np.float32))
    rejector = BackRejector(back_embedding=back_emb)
    assert not rejector.is_back(embedding=other, top_catalog_score=0.99)


def test_back_rejector_disabled_when_no_constant_loaded():
    rejector = BackRejector(back_embedding=None)
    assert not rejector.is_back(embedding=np.zeros(3, dtype=np.float32), top_catalog_score=0.0)
