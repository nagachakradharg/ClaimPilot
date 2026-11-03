import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel, ValidationError

from claimpilot.config import LLMMode
from claimpilot.context import run_context
from claimpilot.llm import FakeResponseMissing, LLMClient, ReplayMiss, fake_responses
from claimpilot.llm import client as client_mod


class Severity(BaseModel):
    severity: str
    confidence: float


MSGS = [HumanMessage("Rear bumper dented in parking lot.")]


async def test_fake_mode_returns_validated_schema(make_settings):
    fake_responses.register("triage", {"severity": "low", "confidence": 0.9})
    llm = LLMClient(make_settings())
    records = []
    llm.add_listener(records.append)

    with run_context("run-123"):
        out = await llm.structured("triage", Severity, MSGS)

    assert out == Severity(severity="low", confidence=0.9)
    assert records[0].source == "fake"
    assert records[0].run_id == "run-123"
    assert records[0].error is None


async def test_fake_response_must_match_schema(make_settings):
    fake_responses.register("triage", {"severity": "low"})  # missing confidence
    with pytest.raises(ValidationError):
        await LLMClient(make_settings()).structured("triage", Severity, MSGS)


async def test_fake_callable_and_file_fixture(make_settings, tmp_path):
    fake_responses.register("echo", lambda msgs: f"saw {len(msgs)} message(s)")
    settings = make_settings()
    assert await LLMClient(settings).text("echo", MSGS) == "saw 1 message(s)"

    (settings.llm_fixtures_dir / "fake").mkdir(parents=True)
    (settings.llm_fixtures_dir / "fake" / "triage.json").write_text(
        '{"severity": "high", "confidence": 0.5}'
    )
    out = await LLMClient(settings).structured("triage", Severity, MSGS)
    assert out.severity == "high"


async def test_missing_fake_is_a_clear_error(make_settings):
    with pytest.raises(FakeResponseMissing, match="unknown_node"):
        await LLMClient(make_settings()).text("unknown_node", MSGS)


async def test_record_then_replay_without_network(make_settings, monkeypatch):
    calls = []

    def fake_build(spec):
        calls.append(spec)
        return GenericFakeChatModel(messages=iter([AIMessage("recorded answer")]))

    monkeypatch.setattr(client_mod, "build_chat_model", fake_build)

    recorder = LLMClient(make_settings(llm_mode=LLMMode.RECORD))
    assert await recorder.text("letter_writer", MSGS) == "recorded answer"
    assert len(calls) == 1

    replayer = LLMClient(make_settings(llm_mode=LLMMode.REPLAY))
    assert await replayer.text("letter_writer", MSGS) == "recorded answer"
    assert len(calls) == 1  # replay did not build/call a model

    with pytest.raises(ReplayMiss):
        await replayer.text("letter_writer", [HumanMessage("a different prompt")])


async def test_retries_then_fails_with_call_error(make_settings, monkeypatch):
    class Flaky:
        attempts = 0

        async def ainvoke(self, *_a, **_k):
            Flaky.attempts += 1
            raise ConnectionError("provider down")

    monkeypatch.setattr(client_mod, "build_chat_model", lambda spec: Flaky())
    monkeypatch.setattr(client_mod.asyncio, "sleep", _no_sleep)
    llm = LLMClient(make_settings(llm_mode=LLMMode.LIVE, llm_max_retries=2))
    records = []
    llm.add_listener(records.append)

    with pytest.raises(client_mod.LLMCallError):
        await llm.text("coverage", MSGS)
    assert Flaky.attempts == 3
    assert records[0].attempts == 3 and records[0].error


async def _no_sleep(_seconds):
    return None
