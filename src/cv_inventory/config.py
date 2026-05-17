"""Runtime config loaded from environment variables (typically via .env)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class R2Config:
    access_key: str
    secret_key: str
    endpoint_url: str
    bucket: str = "tcgplayerapi"


@dataclass(frozen=True)
class Config:
    api_key: str
    catalog_path: Path
    r2: R2Config
    tcgplayer_category: int = 1  # 1 = MTG

    @classmethod
    def from_env(cls) -> Config:
        required = [
            "CV_INVENTORY_API_KEY",
            "CV_INVENTORY_CATALOG_PATH",
            "R2_TCGPLAYER_BUCKET_ACCESS_KEY",
            "R2_TCGPLAYER_BUCKET_SECRET_KEY",
            "R2_TCGPLAYER_URL",
        ]
        missing = [k for k in required if not os.environ.get(k)]
        if missing:
            raise ConfigError(f"Missing required environment variables: {', '.join(missing)}")
        return cls(
            api_key=os.environ["CV_INVENTORY_API_KEY"],
            catalog_path=Path(os.environ["CV_INVENTORY_CATALOG_PATH"]),
            r2=R2Config(
                access_key=os.environ["R2_TCGPLAYER_BUCKET_ACCESS_KEY"],
                secret_key=os.environ["R2_TCGPLAYER_BUCKET_SECRET_KEY"],
                endpoint_url=os.environ["R2_TCGPLAYER_URL"],
            ),
            tcgplayer_category=int(os.environ.get("CV_INVENTORY_TCGPLAYER_CATEGORY", "1")),
        )
