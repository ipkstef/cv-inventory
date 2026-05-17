"""Sync TCGplayer parquet files from R2 to a local cache directory."""

from __future__ import annotations

import logging
from datetime import timezone
from pathlib import Path

import boto3

from cv_inventory.config import R2Config

log = logging.getLogger(__name__)

PARQUET_NAMES = (
    "products.parquet", "skus.parquet", "groups.parquet",
    "conditions.parquet", "printings.parquet", "languages.parquet",
    "rarities.parquet",
)


def _client(cfg: R2Config):
    return boto3.client(
        "s3",
        endpoint_url=cfg.endpoint_url,
        aws_access_key_id=cfg.access_key,
        aws_secret_access_key=cfg.secret_key,
        region_name="auto",
    )


def sync_parquets(cfg: R2Config, category: int, cache_dir: Path) -> dict[str, Path]:
    """Download any parquet whose R2 LastModified is newer than the local mtime.

    Returns a dict mapping each parquet filename to its local path.
    """
    s3 = _client(cfg)
    out_dir = cache_dir / str(category)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}

    for name in PARQUET_NAMES:
        key = f"{category}/{name}"
        local = out_dir / name
        paths[name] = local

        head = s3.head_object(Bucket=cfg.bucket, Key=key)
        remote_mtime = head["LastModified"].astimezone(timezone.utc).timestamp()

        if local.exists() and local.stat().st_mtime >= remote_mtime:
            log.info("Skipping %s (local is fresh)", name)
            continue

        log.info("Downloading %s", name)
        s3.download_file(cfg.bucket, key, str(local))

    return paths
