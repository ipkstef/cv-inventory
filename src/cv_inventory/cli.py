"""Command-line entry points."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cv-inventory")
    sub = parser.add_subparsers(dest="cmd", required=True)

    serve = sub.add_parser("serve", help="Run the FastAPI server")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--env-file", default=".env")
    serve.add_argument(
        "--parquet-cache",
        default="data/cache",
        help="Local directory for cached TCGplayer parquets",
    )
    serve.add_argument(
        "--back-image",
        default=None,
        help="Path to a 448x448 canonical card-back PNG (optional)",
    )

    build = sub.add_parser("build-catalog", help="Build a TCGplayer-keyed embedding catalog")
    build.add_argument("--category", type=int, default=1)
    build.add_argument("--products-parquet", required=True)
    build.add_argument("--out", required=True)
    build.add_argument("--image-cache", default="data/image_cache")

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.cmd == "serve":
        return _serve(args)
    if args.cmd == "build-catalog":
        return _build_catalog(args)
    return 2


def _serve(args) -> int:
    load_dotenv(args.env_file)
    import uvicorn

    from cv_inventory.config import Config
    from cv_inventory.server.app import create_app
    from cv_inventory.server.state import AppState

    config = Config.from_env()
    state = AppState.bootstrap(
        config=config,
        parquet_cache=Path(args.parquet_cache),
        back_image=Path(args.back_image) if args.back_image else None,
    )
    app = create_app(state)
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


def _build_catalog(args) -> int:
    from cv_inventory.catalog_build import build_catalog

    build_catalog(
        products_parquet=Path(args.products_parquet),
        out_path=Path(args.out),
        image_cache=Path(args.image_cache),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
