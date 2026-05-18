"""Singleton app state assembled once at boot."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from collector_vision import Catalog, NeuralEmbedder

from scan_and_identify.back_rejector import BackRejector
from scan_and_identify.config import Config
from scan_and_identify.pipeline import ConfidenceThresholds, IdentifyPipeline
from scan_and_identify.set_index import SetIndex
from scan_and_identify.tcgplayer.r2_sync import sync_parquets
from scan_and_identify.tcgplayer.store import TCGStore


@dataclass
class AppState:
    api_key: str
    catalog: Catalog
    catalog_version: str
    embedder: NeuralEmbedder
    store: TCGStore
    set_index: SetIndex
    pipeline: IdentifyPipeline
    parquet_synced_at: datetime

    @classmethod
    def bootstrap(cls, config: Config, parquet_cache: Path, back_image: Path | None) -> AppState:
        sync_parquets(config.r2, config.tcgplayer_category, parquet_cache)
        parquet_dir = parquet_cache / str(config.tcgplayer_category)
        store = TCGStore.load(parquet_dir)
        catalog = Catalog.load(config.catalog_path)
        embedder = NeuralEmbedder()
        index = SetIndex.build(catalog, store)
        back = BackRejector.load(back_image, embedder)
        pipeline = IdentifyPipeline(
            embedder=embedder,
            index=index,
            store=store,
            back_rejector=back,
            confidence_thresholds=ConfidenceThresholds.from_env(),
        )
        return cls(
            api_key=config.api_key,
            catalog=catalog,
            catalog_version=config.catalog_path.stem,
            embedder=embedder,
            store=store,
            set_index=index,
            pipeline=pipeline,
            parquet_synced_at=datetime.now(UTC),
        )

    @classmethod
    def bootstrap_for_tests(cls, api_key: str, catalog_path: Path, parquet_dir: Path) -> AppState:
        """Same as bootstrap but skips R2 sync — used in tests with synthetic fixtures."""
        store = TCGStore.load(parquet_dir)
        catalog = Catalog.load(catalog_path)
        embedder = NeuralEmbedder()
        index = SetIndex.build(catalog, store)
        back = BackRejector.load(None, embedder)
        pipeline = IdentifyPipeline(embedder=embedder, index=index, store=store, back_rejector=back)
        return cls(
            api_key=api_key,
            catalog=catalog,
            catalog_version=catalog_path.stem,
            embedder=embedder,
            store=store,
            set_index=index,
            pipeline=pipeline,
            parquet_synced_at=datetime.now(UTC),
        )
