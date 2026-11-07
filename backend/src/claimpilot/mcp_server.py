"""ClaimPilot MCP server: the only path from the agents to policy data, claim history,
repair estimates and the legacy core claims system.

It is also the anti-corruption layer: legacy codes/strings from the core system are mapped to
clean typed results here, so graph code never sees `STATUS_CD` or string amounts.

Run standalone (HTTP):  uv run python -m claimpilot.mcp_server --port 8200
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import date
from enum import StrEnum
from functools import lru_cache

import httpx
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import BaseModel

from claimpilot.config import get_settings
from claimpilot.db import get_sessionmaker
from claimpilot.domain import ClaimType
from claimpilot.estimates import RepairEstimates, estimate_repairs
from claimpilot.models import ClaimHistoryProfile, Policy

mcp = FastMCP(
    "claimpilot-tools",
    instructions="Tools for auto-claims processing: policy, history, estimates, core system.",
)


class PolicyInfo(BaseModel):
    policy_number: str
    form_id: str
    status: str
    effective_date: date
    expiry_date: date
    holder_name: str
    vehicle: dict
    vehicle_acv: float
    collision_deductible: float | None
    comprehensive_deductible: float | None


class ClaimHistory(BaseModel):
    policy_number: str
    past_claims: int
    days_policy_to_accident: int
    address_change_recent: bool
    police_report_filed: bool
    witness_present: bool


class VehicleValue(BaseModel):
    vin: str
    acv: float
    source: str
    valued_on: date


class CoreStatus(StrEnum):
    OPEN = "open"
    PROCESSING = "processing"
    PENDING_REVIEW = "pending_review"
    AUTO_APPROVED = "auto_approved"
    APPROVED = "approved"
    DENIED = "denied"
    CLOSED = "closed"
    FAILED = "failed"


STATUS_TO_LEGACY = {
    CoreStatus.OPEN: "OPEN",
    CoreStatus.PROCESSING: "PROC",
    CoreStatus.PENDING_REVIEW: "PEND_REV",
    CoreStatus.AUTO_APPROVED: "AUTO_APPRVD",
    CoreStatus.APPROVED: "APPRVD",
    CoreStatus.DENIED: "DENIED",
    CoreStatus.CLOSED: "CLOSED",
    CoreStatus.FAILED: "FAILED",
}
LEGACY_TO_STATUS = {v: k for k, v in STATUS_TO_LEGACY.items()}
LOSS_TYPE_TO_LEGACY = {
    ClaimType.COLLISION: "COLL",
    ClaimType.THEFT: "THFT",
    ClaimType.GLASS: "GLSS",
    ClaimType.WEATHER: "WTHR",
    ClaimType.OTHER: "OTHR",
}


class CoreClaim(BaseModel):
    core_claim_id: str
    status: CoreStatus
    policy_number: str


@lru_cache
def _core_http() -> httpx.AsyncClient:
    settings = get_settings()
    if settings.core_system_url == "inprocess":
        from claimpilot.core_system.app import app as core_app

        transport = httpx.ASGITransport(app=core_app)
        return httpx.AsyncClient(transport=transport, base_url="http://core", timeout=10)
    return httpx.AsyncClient(base_url=settings.core_system_url, timeout=10)


async def _core(method: str, path: str, **kwargs) -> dict:
    """Call the legacy system; retry 5xx/transport errors with backoff."""
    retries = get_settings().tool_max_retries
    for attempt in range(retries + 1):
        try:
            resp = await _core_http().request(method, path, **kwargs)
            if resp.status_code < 500:
                if resp.status_code >= 400:
                    raise ToolError(f"core system {resp.status_code}: {resp.json()}")
                return resp.json()
        except httpx.TransportError:
            if attempt == retries:
                raise
        if attempt < retries:
            await asyncio.sleep(0.2 * 2**attempt)
    raise ToolError(f"core system unavailable after {retries + 1} attempts: {method} {path}")


def _core_claim(rec: dict) -> CoreClaim:
    return CoreClaim(
        core_claim_id=rec["CLAIM_NO"],
        status=LEGACY_TO_STATUS[rec["STATUS_CD"]],
        policy_number=rec["POLICY_NO"],
    )


@mcp.tool
async def lookup_policy(policy_number: str) -> PolicyInfo:
    """Policy declarations: form, status, period, vehicle, ACV and deductibles."""
    async with get_sessionmaker()() as session:
        p = await session.get(Policy, policy_number)
    if p is None:
        raise ToolError(f"policy {policy_number} not found")
    return PolicyInfo(
        policy_number=p.policy_number,
        form_id=p.form_id,
        status=p.status,
        effective_date=p.effective_date,
        expiry_date=p.expiry_date,
        holder_name=p.holder_name,
        vehicle=p.vehicle,
        vehicle_acv=p.vehicle_acv,
        collision_deductible=p.collision_deductible,
        comprehensive_deductible=p.comprehensive_deductible,
    )


@mcp.tool
async def get_claim_history(policy_number: str) -> ClaimHistory:
    """Prior-claim and timing signals for a policy (used by fraud checks)."""
    async with get_sessionmaker()() as session:
        h = await session.get(ClaimHistoryProfile, policy_number)
    if h is None:
        raise ToolError(f"no claim history for {policy_number}")
    return ClaimHistory(
        policy_number=h.policy_number,
        past_claims=h.past_claims,
        days_policy_to_accident=h.days_policy_to_accident,
        address_change_recent=h.address_change_recent,
        police_report_filed=h.police_report_filed,
        witness_present=h.witness_present,
    )  # fraud_label is ground truth for evals only; never exposed to agents


@mcp.tool
async def get_repair_estimates(
    damage_areas: list[str], severity: str, vehicle_category: str, make: str
) -> RepairEstimates:
    """Quotes from two partner body shops for the listed damage areas."""
    return estimate_repairs(damage_areas, severity, vehicle_category, make)


@mcp.tool
async def get_vehicle_value(vin: str) -> VehicleValue:
    """Actual cash value from the core system's market valuation."""
    rec = await _core("GET", f"/api/v1/vehicles/{vin}/valuation")
    return VehicleValue(
        vin=rec["VIN"],
        acv=float(rec["ACV_AMT"]),
        source=rec["VAL_SRC_CD"].lower(),
        valued_on=date.fromisoformat(rec["VAL_DT"]),
    )


@mcp.tool
async def create_claim_record(
    policy_number: str,
    incident_date: date,
    claim_type: ClaimType,
    description: str,
    external_ref: str,
    claimed_amount: float | None = None,
) -> CoreClaim:
    """Open a claim in the core system. Idempotent on external_ref."""
    rec = await _core(
        "POST",
        "/api/v1/claims",
        json={
            "POLICY_NO": policy_number,
            "LOSS_DT": incident_date.isoformat(),
            "LOSS_TYPE_CD": LOSS_TYPE_TO_LEGACY[claim_type],
            "LOSS_DESC": description[:2000],
            "CLMD_AMT": f"{claimed_amount:.2f}" if claimed_amount is not None else None,
            "EXT_REF": external_ref,
        },
    )
    return _core_claim(rec)


@mcp.tool
async def update_claim_status(
    core_claim_id: str, status: CoreStatus, note: str | None = None
) -> CoreClaim:
    """Move a core-system claim to a new status."""
    rec = await _core(
        "PUT",
        f"/api/v1/claims/{core_claim_id}/status",
        json={"STATUS_CD": STATUS_TO_LEGACY[status], "NOTE_TXT": note},
    )
    return _core_claim(rec)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8200)
    args = parser.parse_args()
    mcp.run(transport="http", host="0.0.0.0", port=args.port)
