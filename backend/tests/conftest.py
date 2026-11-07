import pytest

from claimpilot.config import LLMMode, Settings, get_settings
from claimpilot.llm import fake_responses


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch):
    """Tests never read the developer's .env and never call a real model."""
    monkeypatch.setenv("LLM_MODE", "fake")
    for key in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LLM_NODE_MODELS"):
        monkeypatch.delenv(key, raising=False)
    get_settings.cache_clear()
    fake_responses.clear()
    yield
    get_settings.cache_clear()
    fake_responses.clear()


@pytest.fixture
def make_settings(tmp_path):
    def _make(**overrides) -> Settings:
        base = {"llm_mode": LLMMode.FAKE, "llm_fixtures_dir": tmp_path / "llm"}
        return Settings(_env_file=None, **(base | overrides))

    return _make


TEST_DB = "claimpilot_test"


def _ensure_test_db() -> str | None:
    """Create the throwaway test DB if Postgres is reachable; never touches the dev DB."""
    import psycopg

    url = Settings(_env_file=None).database_url
    try:
        with psycopg.connect(
            url.replace("postgresql+psycopg://", "postgresql://"),
            autocommit=True,
            connect_timeout=2,
        ) as conn:
            if not conn.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s", (TEST_DB,)
            ).fetchone():
                conn.execute(f"CREATE DATABASE {TEST_DB}")
    except psycopg.OperationalError:
        return None
    return f"{url.rsplit('/', 1)[0]}/{TEST_DB}"


TEST_DB_URL = _ensure_test_db()
requires_db = pytest.mark.skipif(TEST_DB_URL is None, reason="Postgres not reachable")


def _clear_caches():
    from claimpilot.db import get_engine, get_sessionmaker
    from claimpilot.embeddings import get_embedder

    for cached in (get_settings, get_engine, get_sessionmaker, get_embedder):
        cached.cache_clear()


@pytest.fixture
async def seeded_db(monkeypatch):
    from sqlalchemy import text

    from claimpilot.db import get_engine
    from claimpilot.models import Base
    from claimpilot.seed import seed

    monkeypatch.setenv("DATABASE_URL", TEST_DB_URL)
    monkeypatch.setenv("EMBEDDING_PROVIDER", "hash")
    _clear_caches()
    async with get_engine().begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
    await seed()
    yield
    await get_engine().dispose()
    _clear_caches()
