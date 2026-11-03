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
