import pytest

from claimpilot.config import LLMMode, Settings
from claimpilot.llm.models import parse_model_spec


def test_defaults_are_zero_cost():
    s = Settings(_env_file=None)
    assert s.llm_mode is LLMMode.FAKE
    assert s.fraud_review_threshold == 0.6
    assert s.payout_review_threshold == 5000.0
    assert s.extraction_confidence_min == 0.7
    assert not s.tracing_enabled


def test_per_node_model_override_from_env(monkeypatch):
    monkeypatch.setenv("LLM_DEFAULT_MODEL", "ollama:gpt-oss:20b")
    monkeypatch.setenv("LLM_NODE_MODELS", '{"intake": "openai:some-vision-model"}')
    s = Settings(_env_file=None)
    assert s.model_for("intake") == "openai:some-vision-model"
    assert s.model_for("coverage") == "ollama:gpt-oss:20b"


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("ollama:gpt-oss:20b", ("ollama", "gpt-oss:20b")),
        ("anthropic:model-x", ("anthropic", "model-x")),
    ],
)
def test_parse_model_spec(spec, expected):
    assert parse_model_spec(spec) == expected


@pytest.mark.parametrize("bad", ["gpt-4", ":model", "openai:"])
def test_parse_model_spec_rejects_bad(bad):
    with pytest.raises(ValueError):
        parse_model_spec(bad)


def test_dev_jwt_secret_rejected_outside_dev():
    import pytest

    from claimpilot.config import Settings

    with pytest.raises(ValueError, match="JWT_SECRET"):
        Settings(env="prod", _env_file=None)
    assert Settings(env="prod", jwt_secret="x" * 40, _env_file=None).env == "prod"
