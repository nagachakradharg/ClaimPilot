from claimpilot.events import Event, EventHub, cost_usd
from claimpilot.redact import known_pii, redact, redact_text

KNOWN = known_pii(
    "Casey Fofana", "casey.fofana@example.com", "555-0103", "134 Cedar Ln, Austin, TX"
)


def test_redacts_known_values_and_patterns():
    text = (
        "Casey Fofana (casey.fofana@example.com, 555-0103) at 134 Cedar Ln. "
        "Policy CP-100003, VIN SYNTH000000000003, call (512) 555-0199. Fofana agreed."
    )
    out = redact_text(text, KNOWN)
    for leaked in (
        "Casey",
        "Fofana",
        "@example.com",
        "555-0103",
        "Cedar",
        "100003",
        "SYNTH",
        "0199",
    ):
        assert leaked not in out, leaked
    assert "CP-10****" in out  # policy numbers are partially masked, not removed


def test_pattern_redaction_without_known_values():
    out = redact_text("Email jo@x.io or call 312-555-0142; lives at 9 Elm Street.")
    assert "jo@x.io" not in out and "0142" not in out and "Elm Street" not in out


def test_redact_walks_nested_structures():
    data = {"a": ["Casey called", {"b": "casey.fofana@example.com"}], "n": 3}
    assert redact(data, KNOWN) == {"a": ["[redacted] called", {"b": "[redacted]"}], "n": 3}


async def test_hub_redacts_before_streaming_and_costs_tokens(monkeypatch):
    hub = EventHub(persist=False)
    hub.register_pii("c1", KNOWN)
    q = hub.subscribe("c1")
    hub.publish(Event(claim_id="c1", kind="tool", name="t", data={"desc": "Casey Fofana"}))
    assert (await q.get()).data == {"desc": "[redacted]"}
    assert cost_usd("ollama:local", 1000, 500) == 0.0
    monkeypatch.setattr(
        "claimpilot.events.get_settings",
        lambda: type("S", (), {"model_prices": {"openai:m": [1.0, 4.0]}})(),
    )
    assert cost_usd("openai:m", 1_000_000, 500_000) == 3.0


def test_reason_key_groups_by_check():
    from claimpilot.api.claims import reason_key

    assert reason_key("flag:quote_not_verbatim:COLL-1") == "flag:quote_not_verbatim"
    assert reason_key("flag:unpriced_damage_areas:rear hatch") == "flag:unpriced_damage_areas"
    assert reason_key("fraud_score_0.77") == "fraud_score"
    assert reason_key("payout_at_or_above_5000") == "payout_at_or_above_5000"
    assert reason_key("low_confidence:cause") == "low_confidence:cause"
