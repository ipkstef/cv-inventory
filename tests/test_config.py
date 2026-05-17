import pytest

from cv_inventory.config import Config, ConfigError


def test_config_from_env_reads_all_fields(monkeypatch, tmp_path):
    catalog = tmp_path / "catalog.npz"
    catalog.write_bytes(b"")
    monkeypatch.setenv("CV_INVENTORY_API_KEY", "secret")
    monkeypatch.setenv("CV_INVENTORY_CATALOG_PATH", str(catalog))
    monkeypatch.setenv("R2_TCGPLAYER_BUCKET_ACCESS_KEY", "ak")
    monkeypatch.setenv("R2_TCGPLAYER_BUCKET_SECRET_KEY", "sk")
    monkeypatch.setenv("R2_TCGPLAYER_URL", "https://example.r2.cloudflarestorage.com")

    cfg = Config.from_env()

    assert cfg.api_key == "secret"
    assert cfg.catalog_path == catalog
    assert cfg.r2.access_key == "ak"
    assert cfg.r2.secret_key == "sk"
    assert cfg.r2.endpoint_url == "https://example.r2.cloudflarestorage.com"
    assert cfg.r2.bucket == "tcgplayerapi"
    assert cfg.tcgplayer_category == 1


def test_config_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("CV_INVENTORY_API_KEY", raising=False)
    monkeypatch.setenv("CV_INVENTORY_CATALOG_PATH", "/nope")
    monkeypatch.setenv("R2_TCGPLAYER_BUCKET_ACCESS_KEY", "ak")
    monkeypatch.setenv("R2_TCGPLAYER_BUCKET_SECRET_KEY", "sk")
    monkeypatch.setenv("R2_TCGPLAYER_URL", "https://e.r2.cloudflarestorage.com")

    with pytest.raises(ConfigError, match="CV_INVENTORY_API_KEY"):
        Config.from_env()
