import pytest

from cv_inventory.pipeline import ConfidenceThresholds, classify_confidence


def test_default_good_when_score_and_gap_high():
    assert classify_confidence(0.70, 0.30) == "good"


def test_default_poor_when_score_low():
    assert classify_confidence(0.40, 0.50) == "poor"


def test_default_poor_when_gap_small():
    assert classify_confidence(0.80, 0.02) == "poor"


def test_default_fair_otherwise():
    assert classify_confidence(0.50, 0.10) == "fair"


def test_boundary_good_is_inclusive():
    # score and gap exactly at GOOD thresholds → good
    assert classify_confidence(0.55, 0.15) == "good"


def test_boundary_poor_score_is_exclusive():
    # score exactly at POOR threshold (0.45) is NOT poor by score
    assert classify_confidence(0.45, 0.10) == "fair"


def test_env_thresholds_override(monkeypatch):
    monkeypatch.setenv("CV_INVENTORY_CONF_GOOD_SCORE", "0.80")
    monkeypatch.setenv("CV_INVENTORY_CONF_GOOD_GAP", "0.30")
    monkeypatch.setenv("CV_INVENTORY_CONF_POOR_SCORE", "0.20")
    monkeypatch.setenv("CV_INVENTORY_CONF_POOR_GAP", "0.01")

    t = ConfidenceThresholds.from_env()

    # 0.70/0.40 would be "good" under defaults, but not under the stricter env
    assert t.classify(0.70, 0.40) == "fair"
    # 0.85/0.35 hits the stricter good threshold
    assert t.classify(0.85, 0.35) == "good"
    # 0.40/0.10 would be "poor" under defaults but is fair under the looser POOR
    assert t.classify(0.40, 0.10) == "fair"


def test_env_thresholds_missing_keep_defaults(monkeypatch):
    for k in [
        "CV_INVENTORY_CONF_GOOD_SCORE",
        "CV_INVENTORY_CONF_GOOD_GAP",
        "CV_INVENTORY_CONF_POOR_SCORE",
        "CV_INVENTORY_CONF_POOR_GAP",
    ]:
        monkeypatch.delenv(k, raising=False)

    t = ConfidenceThresholds.from_env()
    assert t == ConfidenceThresholds()


@pytest.mark.parametrize(
    "score,gap,expected",
    [
        (0.99, 0.50, "good"),
        (0.55, 0.15, "good"),
        (0.55, 0.14, "fair"),
        (0.54, 0.15, "fair"),
        (0.50, 0.10, "fair"),
        (0.44, 0.10, "poor"),
        (0.55, 0.04, "poor"),
    ],
)
def test_table(score, gap, expected):
    assert classify_confidence(score, gap) == expected
