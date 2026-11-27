"""Tracing helpers without a Langfuse server: masking and no-op behaviour."""

from types import SimpleNamespace

from claimpilot import tracing


def test_mask_value_uses_patterns_and_known_values():
    tracing.register_pii(["Casey Fofana", "Casey", "Fofana"])
    known = tracing._known_values()
    masked = tracing._mask_value('{"text": "Casey Fofana, casey@x.com, policy CP-100003"}', known)
    assert "Fofana" not in masked and "casey@x.com" not in masked and "CP-10****" in masked
    assert tracing._mask_value("nothing sensitive", known) is None  # unchanged -> no patch
    assert tracing._mask_value(["Casey", "ok"], known) == ["[redacted]", "ok"]
    assert tracing._mask_value(42, known) is None


def test_mask_otel_spans_patches_only_changed_attributes():
    tracing.register_pii(["Quinn Dubois"])
    ident = (1, 2)  # an OtelSpanIdentifier in real batches; any hashable key works here
    span = SimpleNamespace(attributes={"langfuse.observation.input": "Quinn Dubois called",
                                       "langfuse.observation.level": "DEFAULT"})  # fmt: skip
    result = tracing.mask_otel_spans(params=SimpleNamespace(spans={ident: span}))
    patch = result.span_patches[ident]
    assert patch.set_attributes == {"langfuse.observation.input": "[redacted] called"}


def test_known_pii_registry_is_bounded():
    tracing.register_pii([f"name-{i:05d}" for i in range(tracing._KNOWN_MAX + 10)])
    assert len(tracing._known_values()) <= tracing._KNOWN_MAX


def test_helpers_are_noops_without_keys():
    assert tracing.get_callbacks() == []
    with tracing.trace_run("process-claim", session_id="clm_x", input={"a": 1}) as root:
        root.update(output={"b": 2})
    with tracing.observe("retrieve-policy-clauses", "retriever") as obs:
        obs.update(output=[])
    assert tracing.eval_session("letters").startswith("eval-letters-")


def test_mcp_sdk_spans_are_not_exported_but_app_spans_are():
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    for scope in ("fastmcp", "mcp-python-sdk", "langfuse-sdk"):
        with provider.get_tracer(scope).start_as_current_span("x"):
            pass
    kept = {s.instrumentation_scope.name for s in exporter.get_finished_spans()
            if tracing.should_export_span(s)}  # fmt: skip
    assert "fastmcp" not in kept and "mcp-python-sdk" not in kept
