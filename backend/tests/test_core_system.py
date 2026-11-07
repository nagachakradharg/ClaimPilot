import httpx
import pytest

from claimpilot import mcp_server
from claimpilot.core_system.app import create_core_app

CLAIM = {
    "POLICY_NO": "CP-1",
    "LOSS_DT": "2026-03-04",
    "LOSS_TYPE_CD": "COLL",
    "LOSS_DESC": "rear-ended",
    "EXT_REF": "clm_1",
}


def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://core")


async def test_legacy_claim_lifecycle_and_idempotency():
    async with _client(create_core_app(fault_rate=0)) as c:
        first = (await c.post("/api/v1/claims", json=CLAIM)).json()
        again = (await c.post("/api/v1/claims", json=CLAIM)).json()
        assert first["CLAIM_NO"] == again["CLAIM_NO"]  # same EXT_REF -> same record
        assert first["STATUS_CD"] == "OPEN"
        upd = await c.put(f"/api/v1/claims/{first['CLAIM_NO']}/status", json={"STATUS_CD": "PROC"})
        assert len(upd.json()["HIST"]) == 1 and upd.json()["STATUS_CD"] == "PROC"
        bad = await c.put(f"/api/v1/claims/{first['CLAIM_NO']}/status", json={"STATUS_CD": "nope"})
        assert bad.status_code == 422


async def test_mcp_layer_retries_injected_503s(monkeypatch):
    """Fault injection: a flaky core system still succeeds through the retry layer."""
    flaky = create_core_app(fault_rate=0, fail_first=2)  # 2 x 503, then healthy
    monkeypatch.setattr(mcp_server, "_core_http", lambda: _client(flaky))
    monkeypatch.setattr(mcp_server.asyncio, "sleep", _no_sleep)
    first = await mcp_server._core("POST", "/api/v1/claims", json=CLAIM)
    again = await mcp_server._core("POST", "/api/v1/claims", json=CLAIM)
    assert first["CLAIM_NO"] == again["CLAIM_NO"]  # retried + idempotent


async def test_mcp_layer_gives_up_when_core_is_down(monkeypatch):
    down = create_core_app(fault_rate=1.0)
    monkeypatch.setattr(mcp_server, "_core_http", lambda: _client(down))
    monkeypatch.setattr(mcp_server.asyncio, "sleep", _no_sleep)
    with pytest.raises(mcp_server.ToolError, match="unavailable"):
        await mcp_server._core("GET", "/api/v1/claims/X")


def test_status_mapping_is_complete_and_reversible():
    assert set(mcp_server.LEGACY_TO_STATUS) == set(mcp_server.STATUS_TO_LEGACY.values())
    assert len(mcp_server.STATUS_TO_LEGACY) == len(mcp_server.CoreStatus)


async def _no_sleep(_):
    return None
