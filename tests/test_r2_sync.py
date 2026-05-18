from __future__ import annotations

import boto3
import pytest
from moto import mock_aws

from scan_and_identify.config import R2Config
from scan_and_identify.tcgplayer.r2_sync import sync_parquets

PARQUET_NAMES = [
    "products.parquet",
    "skus.parquet",
    "groups.parquet",
    "conditions.parquet",
    "printings.parquet",
    "languages.parquet",
    "rarities.parquet",
]


@pytest.fixture
def fake_r2(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("MOTO_S3_CUSTOM_ENDPOINTS", "http://fake.r2.cloudflarestorage.com")
    with mock_aws():
        s3 = boto3.client("s3", endpoint_url="http://fake.r2.cloudflarestorage.com")
        s3.create_bucket(Bucket="tcgplayerapi")
        for name in PARQUET_NAMES:
            s3.put_object(Bucket="tcgplayerapi", Key=f"1/{name}", Body=b"FAKE-" + name.encode())
        yield s3


def test_sync_downloads_all_seven_parquets(fake_r2, tmp_path):
    cfg = R2Config(
        access_key="ak", secret_key="sk", endpoint_url="http://fake.r2.cloudflarestorage.com"
    )
    cache = tmp_path / "cache"

    paths = sync_parquets(cfg, category=1, cache_dir=cache)

    for name in PARQUET_NAMES:
        assert paths[name].exists()
        assert paths[name].read_bytes().startswith(b"FAKE-")


def test_sync_skips_when_local_is_fresh(fake_r2, tmp_path):
    cfg = R2Config(
        access_key="ak", secret_key="sk", endpoint_url="http://fake.r2.cloudflarestorage.com"
    )
    cache = tmp_path / "cache"

    sync_parquets(cfg, category=1, cache_dir=cache)
    original_mtime = (cache / "1" / "products.parquet").stat().st_mtime

    sync_parquets(cfg, category=1, cache_dir=cache)
    assert (cache / "1" / "products.parquet").stat().st_mtime == original_mtime
