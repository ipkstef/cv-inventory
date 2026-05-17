FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        git libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --upgrade pip && pip install .

# Catalog is baked in at build time. Set CV_INVENTORY_CATALOG_PATH=/app/catalogs/<file>.npz
COPY catalogs/ ./catalogs/

EXPOSE 8000

CMD ["cv-inventory", "serve", "--host", "0.0.0.0", "--port", "8000", \
     "--parquet-cache", "/app/data/cache"]
