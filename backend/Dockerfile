# ClaimPilot backend image: API, mock core system and MCP server share it (different commands).
# Build context: repository root (it needs ../data for policy forms and the core-system data).
FROM python:3.12-slim AS base
COPY --from=ghcr.io/astral-sh/uv:0.9.5 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1 FASTEMBED_CACHE_PATH=/opt/fastembed
WORKDIR /app/backend

# Dependencies first (cached layer), then the project.
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/ ./
RUN uv sync --frozen --no-dev
ENV PATH="/app/backend/.venv/bin:$PATH"

# Bake the embedding model into the image (no download at runtime).
RUN python -c "from claimpilot.embeddings import FastEmbedder; from claimpilot.config import get_settings; FastEmbedder(get_settings().embedding_model).embed_query('warmup')"

# Policy forms, demo photos and core-system reference data (raw third-party data excluded).
COPY data/ /app/data/

RUN useradd --create-home --uid 10001 app && mkdir -p /app/storage && chown -R app /app/storage /app/data /opt/fastembed
USER app
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn claimpilot.api.main:app --host 0.0.0.0 --port 8000 --proxy-headers"]
