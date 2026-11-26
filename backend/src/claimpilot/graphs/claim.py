"""Parent claim graph: composes the subgraphs with deterministic routing (ADR-0001).

    intake --(invalid)--> END
       \\-> register_core -> [coverage || fraud] -> payout? -> gate
              gate --(any review reason)--> human_review (interrupt) --\\
                   \\--(none)--> auto_approve -------------------------------> letter -> close

State is checkpointed in Postgres (thread_id = claim_id), so a claim paused at human_review
survives a restart and resumes there without re-running earlier steps.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Callable
from enum import StrEnum
from typing import Any, Literal, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from claimpilot.config import Settings, get_settings
from claimpilot.context import run_context
from claimpilot.domain import ClaimForm, ClaimType, CoverageDecision, CoveragePart, CoverageResult
from claimpilot.estimates import RepairEstimates
from claimpilot.graphs.coverage import build_coverage_graph
from claimpilot.graphs.fraud import FraudResult, build_fraud_graph
from claimpilot.graphs.intake import IntakeResult, build_intake_graph
from claimpilot.graphs.letter import (
    Decision,
    Letter,
    LetterEdit,
    LetterInput,
    build_letter_graph,
)
from claimpilot.llm import LLMClient
from claimpilot.mcp_server import CoreClaim, VehicleValue
from claimpilot.payout import PayoutResult, compute_payout
from claimpilot.photos import StoredPhoto, store_photo, validate_photo
from claimpilot.tools import ToolClient


class ClaimStatus(StrEnum):
    INVALID = "invalid"  # rejected at intake validation; never entered the lifecycle
    PROCESSING = "processing"
    PENDING_REVIEW = "pending_review"
    PENDING_LETTER = "pending_letter"  # letter failed its checks twice; an adjuster edits it
    CLOSED = "closed"  # decided (see ClaimRun.decision) and letter produced
    FAILED = "failed"


class PayoutOutcome(BaseModel):
    vehicle_value: VehicleValue
    estimates: RepairEstimates | None
    result: PayoutResult
    flags: list[str] = Field(default_factory=list)


class ReviewDecision(BaseModel):
    """Adjuster action at the review gate."""

    action: Literal["approve", "deny", "override"]
    reason: str | None = None
    payout_amount: float | None = Field(default=None, ge=0)
    adjuster: str = "adjuster"

    @model_validator(mode="after")
    def _check(self) -> ReviewDecision:
        if self.action in ("deny", "override") and not (self.reason or "").strip():
            raise ValueError(f"a reason is required to {self.action}")
        if self.action == "override" and self.payout_amount is None:
            raise ValueError("override requires payout_amount")
        return self


class ClaimRun(BaseModel):
    """Everything a claim run produced (persisted via the checkpointer)."""

    claim_id: str
    status: ClaimStatus
    decision: Decision | None = None
    final_payout: float | None = None
    core_claim_id: str | None = None
    intake: IntakeResult | None = None
    coverage: CoverageResult | None = None
    fraud: FraudResult | None = None
    payout: PayoutOutcome | None = None
    review_reasons: list[str] = Field(default_factory=list)
    review: ReviewDecision | None = None
    letter: Letter | None = None
    error: str | None = None


class ClaimState(TypedDict, total=False):
    claim_id: str
    form: ClaimForm
    photos: list[StoredPhoto]
    upload_errors: list[str]
    intake: IntakeResult
    core_claim_id: str
    coverage: CoverageResult
    fraud: FraudResult
    payout: PayoutOutcome
    review_reasons: list[str]
    review: ReviewDecision
    decision: Decision
    final_payout: float | None
    letter: Letter
    status: ClaimStatus


def review_reasons(state: ClaimState, settings: Settings) -> list[str]:
    """Human review gate. Every triggered reason is returned (the adjuster sees all)."""
    reasons: list[str] = []
    cov = state.get("coverage")
    if cov is None or cov.decision is not CoverageDecision.COVERED:
        reasons.append(f"coverage_{cov.decision.value if cov else 'missing'}")
    fraud = state.get("fraud")
    if fraud and fraud.score >= settings.fraud_review_threshold:
        reasons.append(f"fraud_score_{fraud.score:.2f}")
    payout = state.get("payout")
    if payout and payout.result.payout >= settings.payout_review_threshold:
        reasons.append(f"payout_at_or_above_{settings.payout_review_threshold:.0f}")
    intake = state["intake"]
    reasons += [f"low_confidence:{f}" for f in intake.low_confidence_fields]
    flags = intake.flags + (cov.flags if cov else []) + (payout.flags if payout else [])
    reasons += [f"flag:{f}" for f in flags]
    return list(dict.fromkeys(reasons))  # de-duplicate, keep order


def build_claim_graph(
    llm: LLMClient,
    tools: ToolClient,
    sessionmaker: async_sessionmaker[AsyncSession],
    checkpointer: BaseCheckpointSaver | None = None,
):
    settings = get_settings()
    intake_graph = build_intake_graph(llm, tools)
    coverage_graph = build_coverage_graph(llm, sessionmaker)
    fraud_graph = build_fraud_graph(llm, tools)
    letter_graph = build_letter_graph(llm)

    async def set_core_status(state: ClaimState, status: str, note: str | None = None) -> None:
        await tools.call(
            "update_claim_status",
            CoreClaim,
            core_claim_id=state["core_claim_id"],
            status=status,
            note=(note or "")[:500] or None,
        )

    async def intake(state: ClaimState) -> dict[str, Any]:
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

    async def register_core(state: ClaimState) -> dict[str, Any]:
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

    async def coverage(state: ClaimState) -> dict[str, Any]:
        out = await coverage_graph.ainvoke({"facts": state["intake"].facts})
        return {"coverage": out["result"]}

    async def fraud(state: ClaimState) -> dict[str, Any]:
        i = state["intake"]
        out = await fraud_graph.ainvoke(
            {
                "form": state["form"],
                "extraction": i.extraction,
                "policy": i.policy,
                "photos": state["photos"],
                "checks": [],
            }
        )
        return {"fraud": out["result"]}

    async def payout(state: ClaimState) -> dict[str, Any]:
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

    async def gate(state: ClaimState) -> dict[str, Any]:
        reasons = review_reasons(state, settings)
        if reasons:
            await set_core_status(state, "pending_review", "; ".join(reasons))
            return {"review_reasons": reasons, "status": ClaimStatus.PENDING_REVIEW}
        return {"review_reasons": [], "status": ClaimStatus.PROCESSING}

    async def human_review(state: ClaimState) -> dict[str, Any]:
        # Pauses here; the adjuster's ReviewDecision is the resume value.
        # Nothing with side effects may run before interrupt(): the node re-runs on resume.
        raw = interrupt({"claim_id": state["claim_id"], "review_reasons": state["review_reasons"]})
        review = ReviewDecision.model_validate(raw)
        payout_out = state.get("payout")
        if review.action == "deny":
            decision, amount = Decision.DENIED, None
        elif review.action == "override":
            decision, amount = Decision.APPROVED, review.payout_amount
        else:
            if payout_out is None:
                raise ValueError("approve needs a computed payout; use override with an amount")
            decision, amount = Decision.APPROVED, payout_out.result.payout
        await set_core_status(state, decision.value, f"{review.adjuster}: {review.reason or ''}")
        return {"review": review, "decision": decision, "final_payout": amount}

    async def auto_approve(state: ClaimState) -> dict[str, Any]:
        await set_core_status(state, "auto_approved")
        return {"decision": Decision.AUTO_APPROVED, "final_payout": state["payout"].result.payout}

    async def letter(state: ClaimState) -> dict[str, Any]:
        i, cov, dec = state["intake"], state.get("coverage"), state["decision"]
        review = state.get("review")
        if dec is Decision.DENIED:
            reason = review.reason
        elif review and review.action == "override":
            reason = f"Approved after adjuster review. {review.reason}"
        else:
            part = cov.coverage_part.value if cov else "physical damage"
            reason = f"The loss is covered under your {part} coverage."
        payout_out = state.get("payout")
        record = LetterInput(
            decision=dec,
            claimant_name=i.policy.holder_name,
            claim_number=state["core_claim_id"],
            incident_date=i.facts.incident_date.isoformat(),
            claim_type=i.facts.claim_type.value,
            citations=cov.citations if cov else [],
            reason=reason,
            payout=state.get("final_payout") if dec is not Decision.DENIED else None,
            deductible=payout_out.result.deductible_applied
            if payout_out and dec is not Decision.DENIED
            else None,
        )
        out = await letter_graph.ainvoke({"record": record})
        if out["letter"].needs_adjuster_edit:  # never sent as is
            return {"letter": out["letter"], "status": ClaimStatus.PENDING_LETTER}
        return {"letter": out["letter"]}

    async def letter_edit(state: ClaimState) -> dict[str, Any]:
        # Pauses for the adjuster's corrected letter (resume value: LetterEdit).
        raw = interrupt({"claim_id": state["claim_id"], "issues": state["letter"].issues})
        edit = LetterEdit.model_validate(raw)
        edited = state["letter"].model_copy(
            update={"subject": edit.subject, "body": edit.body, "needs_adjuster_edit": False,
                    "edited_by": edit.adjuster}
        )  # fmt: skip
        return {"letter": edited, "status": ClaimStatus.PROCESSING}

    async def close(state: ClaimState) -> dict[str, Any]:
        await set_core_status(state, "closed")
        return {"status": ClaimStatus.CLOSED}

    g = StateGraph(ClaimState)
    for name, fn in [
        ("intake", intake),
        ("register_core", register_core),
        ("coverage", coverage),
        ("fraud", fraud),
        ("payout", payout),
        ("gate", gate),
        ("human_review", human_review),
        ("auto_approve", auto_approve),
        ("letter", letter),
        ("letter_edit", letter_edit),
        ("close", close),
    ]:
        g.add_node(name, fn)
    g.add_edge(START, "intake")
    g.add_conditional_edges(
        "intake",
        lambda s: END if s["status"] is ClaimStatus.INVALID else "register_core",
        ["register_core", END],
    )
    g.add_edge("register_core", "coverage")  # coverage and fraud run in parallel
    g.add_edge("register_core", "fraud")
    g.add_node("after_checks", lambda s: {})  # join point
    g.add_edge(["coverage", "fraud"], "after_checks")
    g.add_conditional_edges(
        "after_checks",
        lambda s: "payout" if s["coverage"].decision is CoverageDecision.COVERED else "gate",
        ["payout", "gate"],
    )
    g.add_edge("payout", "gate")
    g.add_conditional_edges(
        "gate",
        lambda s: "human_review" if s["review_reasons"] else "auto_approve",
        ["human_review", "auto_approve"],
    )
    g.add_edge("human_review", "letter")
    g.add_edge("auto_approve", "letter")
    g.add_conditional_edges(
        "letter",
        lambda s: "letter_edit" if s["letter"].needs_adjuster_edit else "close",
        ["letter_edit", "close"],
    )
    g.add_edge("letter_edit", "close")
    g.add_edge("close", END)
    return g.compile(name="claim", checkpointer=checkpointer)


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


class ClaimService:
    """Start, resume and inspect claim runs. One instance per process (API, CLI, tests)."""

    def __init__(
        self,
        llm: LLMClient,
        tools: ToolClient,
        sessionmaker: async_sessionmaker[AsyncSession],
        checkpointer: BaseCheckpointSaver,
        on_graph: Callable[[str, tuple[str, ...], dict[str, Any]], None] | None = None,
    ) -> None:
        self.settings = get_settings()
        self.tools = tools
        self.on_graph = on_graph  # node start/finish events (audit + live progress)
        self.graph = build_claim_graph(llm, tools, sessionmaker, checkpointer)

    @staticmethod
    def new_claim_id() -> str:
        return f"clm_{uuid.uuid4().hex[:12]}"

    @staticmethod
    def _config(claim_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": claim_id}}

    async def start(
        self, form: ClaimForm, photos: list[tuple[str, bytes]], claim_id: str | None = None
    ) -> ClaimRun:
        claim_id = claim_id or self.new_claim_id()
        stored, upload_errors = prepare_photos(photos, claim_id, self.settings)
        initial: ClaimState = {
            "claim_id": claim_id,
            "form": form,
            "photos": stored,
            "upload_errors": upload_errors,
        }
        return await self._drive(claim_id, initial)

    async def resume(self, claim_id: str, review: ReviewDecision) -> ClaimRun:
        current = await self.get(claim_id)
        if current.status is not ClaimStatus.PENDING_REVIEW:
            raise ValueError(f"claim {claim_id} is {current.status.value}, not pending_review")
        return await self._drive(claim_id, Command(resume=review.model_dump()))

    async def edit_letter(self, claim_id: str, edit: LetterEdit) -> ClaimRun:
        current = await self.get(claim_id)
        if current.status is not ClaimStatus.PENDING_LETTER:
            raise ValueError(f"claim {claim_id} is {current.status.value}, not pending_letter")
        return await self._drive(claim_id, Command(resume=edit.model_dump()))

    async def get(self, claim_id: str) -> ClaimRun:
        snap = await self.graph.aget_state(self._config(claim_id))
        if not snap.values:
            raise KeyError(claim_id)
        return self._to_run(claim_id, snap.values)

    async def _drive(self, claim_id: str, graph_input: Any) -> ClaimRun:
        error = None
        with run_context(claim_id):
            try:
                async for ns, chunk in self.graph.astream(
                    graph_input, self._config(claim_id), stream_mode="debug", subgraphs=True
                ):
                    if self.on_graph:
                        self.on_graph(claim_id, ns, chunk)
            except Exception as exc:  # terminal failure after retries
                error = f"{type(exc).__name__}: {exc}"
        state = (await self.graph.aget_state(self._config(claim_id))).values
        if error and (core_id := state.get("core_claim_id")):
            with contextlib.suppress(Exception):  # best effort; the failure is the headline
                await self.tools.call(
                    "update_claim_status",
                    CoreClaim,
                    core_claim_id=core_id,
                    status="failed",
                    note=error[:500],
                )
        return self._to_run(claim_id, state, error)

    @staticmethod
    def _to_run(claim_id: str, state: dict[str, Any], error: str | None = None) -> ClaimRun:
        return ClaimRun(
            claim_id=claim_id,
            status=ClaimStatus.FAILED if error else state.get("status", ClaimStatus.PROCESSING),
            decision=state.get("decision"),
            final_payout=state.get("final_payout"),
            core_claim_id=state.get("core_claim_id"),
            intake=state.get("intake"),
            coverage=state.get("coverage"),
            fraud=state.get("fraud"),
            payout=state.get("payout"),
            review_reasons=state.get("review_reasons", []),
            review=state.get("review"),
            letter=state.get("letter"),
            error=error,
        )
