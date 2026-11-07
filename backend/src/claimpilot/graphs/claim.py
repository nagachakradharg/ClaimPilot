"""Parent claim graph: composes the subgraphs with deterministic routing (ADR-0001).

    intake --(invalid)--> END
          \\-> register_core -> coverage --(covered)--> payout -> gate -> END
                                        \\-------------------------/

Week 3 adds: fraud (parallel) before the gate, interrupt/resume at the gate, decision letter.
"""

from __future__ import annotations

import contextlib
import uuid
from enum import StrEnum
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from claimpilot.config import Settings, get_settings
from claimpilot.context import run_context
from claimpilot.domain import ClaimForm, ClaimType, CoverageDecision, CoveragePart, CoverageResult
from claimpilot.estimates import RepairEstimates
from claimpilot.graphs.coverage import build_coverage_graph
from claimpilot.graphs.intake import IntakeResult, build_intake_graph
from claimpilot.llm import LLMClient
from claimpilot.mcp_server import CoreClaim, VehicleValue
from claimpilot.payout import PayoutResult, compute_payout
from claimpilot.photos import StoredPhoto, store_photo, validate_photo
from claimpilot.tools import ToolClient


class ClaimStatus(StrEnum):
    INVALID = "invalid"  # rejected at intake validation; never entered the lifecycle
    PROCESSING = "processing"
    PENDING_REVIEW = "pending_review"
    AUTO_APPROVED = "auto_approved"
    FAILED = "failed"


class PayoutOutcome(BaseModel):
    vehicle_value: VehicleValue
    estimates: RepairEstimates | None
    result: PayoutResult
    flags: list[str] = Field(default_factory=list)


class ClaimRun(BaseModel):
    """Everything a claim run produced. Persisted and shown in the UI from week 4."""

    claim_id: str
    status: ClaimStatus
    core_claim_id: str | None = None
    intake: IntakeResult | None = None
    coverage: CoverageResult | None = None
    payout: PayoutOutcome | None = None
    review_reasons: list[str] = Field(default_factory=list)
    error: str | None = None


class ClaimState(TypedDict, total=False):
    claim_id: str
    form: ClaimForm
    photos: list[StoredPhoto]
    upload_errors: list[str]
    intake: IntakeResult
    core_claim_id: str
    coverage: CoverageResult
    payout: PayoutOutcome
    review_reasons: list[str]
    status: ClaimStatus


def review_reasons(state: ClaimState, settings: Settings) -> list[str]:
    """Human review gate. Every triggered reason is returned (the adjuster sees all)."""
    reasons: list[str] = []
    cov = state.get("coverage")
    if cov is None or cov.decision is not CoverageDecision.COVERED:
        reasons.append(f"coverage_{cov.decision.value if cov else 'missing'}")
    payout = state.get("payout")
    if payout and payout.result.payout >= settings.payout_review_threshold:
        reasons.append(f"payout_at_or_above_{settings.payout_review_threshold:.0f}")
    intake = state["intake"]
    reasons += [f"low_confidence:{f}" for f in intake.low_confidence_fields]
    flags = intake.flags + (cov.flags if cov else []) + (payout.flags if payout else [])
    reasons += [f"flag:{f}" for f in flags]
    # fraud_score >= FRAUD_REVIEW_THRESHOLD is added with the fraud subgraph (week 3)
    return reasons


def build_claim_graph(
    llm: LLMClient, tools: ToolClient, sessionmaker: async_sessionmaker[AsyncSession]
):
    settings = get_settings()
    intake_graph = build_intake_graph(llm, tools)
    coverage_graph = build_coverage_graph(llm, sessionmaker)

    async def intake(state: ClaimState) -> ClaimState:
        out = await intake_graph.ainvoke(
            {
                "form": state["form"],
                "photos": state.get("photos", []),
                "upload_errors": state.get("upload_errors", []),
            }
        )
        result: IntakeResult = out["result"]
        return {
            "intake": result,
            "status": ClaimStatus.INVALID if result.errors else ClaimStatus.PROCESSING,
        }

    async def register_core(state: ClaimState) -> ClaimState:
        facts = state["intake"].facts
        rec = await tools.call(
            "create_claim_record",
            CoreClaim,
            policy_number=facts.policy_number,
            incident_date=facts.incident_date.isoformat(),
            claim_type=facts.claim_type.value,
            description=facts.description,
            claimed_amount=facts.claimed_amount,
            external_ref=state["claim_id"],
        )
        await tools.call(
            "update_claim_status", CoreClaim, core_claim_id=rec.core_claim_id, status="processing"
        )
        return {"core_claim_id": rec.core_claim_id}

    async def coverage(state: ClaimState) -> ClaimState:
        out = await coverage_graph.ainvoke({"facts": state["intake"].facts})
        return {"coverage": out["result"]}

    async def payout(state: ClaimState) -> ClaimState:
        facts, policy = state["intake"].facts, state["intake"].policy
        cov, ex = state["coverage"], state["intake"].extraction
        value = await tools.call("get_vehicle_value", VehicleValue, vin=policy.vehicle["vin"])
        flags: list[str] = []
        whole_vehicle_theft = facts.claim_type is ClaimType.THEFT and any(
            "entire" in a.lower() for a in facts.damage_areas
        )
        if whole_vehicle_theft:
            estimates, amount, waive = None, value.acv, False
        else:
            estimates = await tools.call(
                "get_repair_estimates",
                RepairEstimates,
                damage_areas=facts.damage_areas,
                severity=ex.severity.value,
                vehicle_category=policy.vehicle.get("category", "sedan"),
                make=policy.vehicle.get("make", ""),
            )
            if estimates.unpriced_areas:
                flags.append("unpriced_damage_areas:" + ",".join(estimates.unpriced_areas))
            amount = estimates.lowest_total
            waive = cov.coverage_part is CoveragePart.COMPREHENSIVE and estimates.chip_repair_only
        result = compute_payout(
            amount,
            cov.deductible or 0.0,
            value.acv,
            total_loss_threshold=settings.total_loss_threshold,
            waive_deductible=waive,
        )
        return {
            "payout": PayoutOutcome(
                vehicle_value=value, estimates=estimates, result=result, flags=flags
            )
        }

    async def gate(state: ClaimState) -> ClaimState:
        reasons = review_reasons(state, settings)
        status = ClaimStatus.PENDING_REVIEW if reasons else ClaimStatus.AUTO_APPROVED
        await tools.call(
            "update_claim_status",
            CoreClaim,
            core_claim_id=state["core_claim_id"],
            status=status.value,
            note="; ".join(reasons)[:500] or None,
        )
        return {"review_reasons": reasons, "status": status}

    g = StateGraph(ClaimState)
    for name, fn in [
        ("intake", intake),
        ("register_core", register_core),
        ("coverage", coverage),
        ("payout", payout),
        ("gate", gate),
    ]:
        g.add_node(name, fn)
    g.add_edge(START, "intake")
    g.add_conditional_edges(
        "intake",
        lambda s: END if s["status"] is ClaimStatus.INVALID else "register_core",
        ["register_core", END],
    )
    g.add_edge("register_core", "coverage")
    g.add_conditional_edges(
        "coverage",
        lambda s: "payout" if s["coverage"].decision is CoverageDecision.COVERED else "gate",
        ["payout", "gate"],
    )
    g.add_edge("payout", "gate")
    g.add_edge("gate", END)
    return g.compile(name="claim")


def prepare_photos(
    photos: list[tuple[str, bytes]], claim_id: str, settings: Settings
) -> tuple[list[StoredPhoto], list[str]]:
    """Validate and store uploads before the graph runs (no LLM involved)."""
    stored, errors = [], []
    for name, data in photos:
        if err := validate_photo(data, name, settings.max_photo_bytes):
            errors.append(err)
        else:
            stored.append(store_photo(data, settings.photo_storage_dir / claim_id))
    return stored, errors


async def run_claim(
    form: ClaimForm,
    photos: list[tuple[str, bytes]],
    llm: LLMClient,
    tools: ToolClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    claim_id: str | None = None,
) -> ClaimRun:
    settings = get_settings()
    claim_id = claim_id or f"clm_{uuid.uuid4().hex[:12]}"
    stored, upload_errors = prepare_photos(photos, claim_id, settings)
    graph = build_claim_graph(llm, tools, sessionmaker)
    state: ClaimState = {}
    error = None
    initial: ClaimState = {
        "claim_id": claim_id,
        "form": form,
        "photos": stored,
        "upload_errors": upload_errors,
    }
    with run_context(claim_id):
        try:
            async for snapshot in graph.astream(initial, stream_mode="values"):
                state = snapshot  # keep the latest state so a failure keeps partial results
        except Exception as exc:  # terminal failure after node-level retries
            error = f"{type(exc).__name__}: {exc}"
            if core_id := state.get("core_claim_id"):
                with contextlib.suppress(Exception):  # best effort; the failure is the headline
                    await tools.call(
                        "update_claim_status",
                        CoreClaim,
                        core_claim_id=core_id,
                        status="failed",
                        note=error[:500],
                    )
    return ClaimRun(
        claim_id=claim_id,
        status=ClaimStatus.FAILED if error else state["status"],
        core_claim_id=state.get("core_claim_id"),
        intake=state.get("intake"),
        coverage=state.get("coverage"),
        payout=state.get("payout"),
        review_reasons=state.get("review_reasons", []),
        error=error,
    )
