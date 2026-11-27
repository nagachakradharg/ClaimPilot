"""Claims API: role-scoped CRUD, review actions, audit, SSE progress, metrics.

Every endpoint checks the caller's role server-side. Claimants only ever see their own
claims, and only claimant-safe fields (no fraud details, no internal review reasons).
"""

from __future__ import annotations

import asyncio
import json
import re
import statistics
import time
from collections import Counter
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select
from sse_starlette import EventSourceResponse

from claimpilot.auth import CurrentUser, Role, current_user, require
from claimpilot.config import get_settings
from claimpilot.db import get_sessionmaker
from claimpilot.domain import ClaimForm
from claimpilot.events import Event, EventHub
from claimpilot.graphs.claim import ClaimRun, ClaimService, ClaimStatus, ReviewDecision
from claimpilot.graphs.letter import LetterEdit
from claimpilot.models import AuditEvent, ClaimRecord, Policy
from claimpilot.photos import display_jpeg
from claimpilot.redact import known_pii

router = APIRouter(prefix="/claims", tags=["claims"])
metrics_router = APIRouter(tags=["metrics"])
STAFF = (Role.ADJUSTER, Role.AUDITOR)
TERMINAL = {"invalid", "pending_review", "pending_letter", "closed", "failed"}  # pauses + ends


def _svc(request: Request) -> ClaimService:
    return request.app.state.service


def _hub(request: Request) -> EventHub:
    return request.app.state.hub


async def _record(claim_id: str, user: CurrentUser) -> ClaimRecord:
    async with get_sessionmaker()() as session:
        rec = await session.get(ClaimRecord, claim_id)
    # Claimants get 404 (not 403) for other people's claims: no claim-id enumeration.
    if rec is None or (user.role is Role.CLAIMANT and rec.owner_id != user.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Claim not found")
    return rec


def _summary(rec: ClaimRecord, user: CurrentUser) -> dict[str, Any]:
    out = {
        "claim_id": rec.claim_id,
        "policy_number": rec.policy_number,
        "status": rec.status,
        "decision": rec.decision,
        "final_payout": rec.final_payout,
        "created_at": rec.created_at,
        "updated_at": rec.updated_at,
    }
    if user.role is not Role.CLAIMANT:
        out |= {"review_reasons": rec.review_reasons, "core_claim_id": rec.core_claim_id,
                "assigned_to": rec.assigned_to,
                "processing_ms": rec.processing_ms, "error": rec.error}  # fmt: skip
    return out


async def _save(run: ClaimRun, elapsed_ms: float) -> None:
    async with get_sessionmaker()() as session, session.begin():
        rec = await session.get(ClaimRecord, run.claim_id)
        rec.status = run.status.value
        rec.decision = run.decision.value if run.decision else None
        rec.final_payout = run.final_payout
        rec.review_reasons = run.review_reasons
        rec.core_claim_id = run.core_claim_id
        rec.error = run.error
        rec.processing_ms = (rec.processing_ms or 0) + elapsed_ms


async def _execute(request: Request, claim_id: str, coro) -> None:
    """Run a claim step in the background and record the outcome (status + audit)."""
    hub, start = _hub(request), time.perf_counter()
    try:
        run: ClaimRun = await coro
    except Exception as exc:  # defensive: ClaimService already converts failures to FAILED
        run = ClaimRun(claim_id=claim_id, status=ClaimStatus.FAILED, error=str(exc))
    await _save(run, (time.perf_counter() - start) * 1000)
    hub.publish(Event(claim_id=claim_id, kind="status", name=run.status.value,
                      data={"decision": run.decision.value if run.decision else None,
                            "review_reasons": run.review_reasons, "error": run.error}))  # fmt: skip


def _spawn(request: Request, coro) -> None:
    tasks: set[asyncio.Task] = request.app.state.tasks
    task = asyncio.create_task(coro)
    tasks.add(task)
    task.add_done_callback(tasks.discard)


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def submit_claim(
    request: Request,
    user: Annotated[CurrentUser, Depends(require(Role.CLAIMANT))],
    form: Annotated[str, Form(description="ClaimForm as JSON")],
    photos: Annotated[list[UploadFile], File(description="1-6 JPEG/PNG photos")],
) -> dict[str, Any]:
    try:
        claim_form = ClaimForm.model_validate_json(form)
    except ValidationError as exc:
        raise HTTPException(422, json.loads(exc.json(include_url=False))) from exc
    if claim_form.policy_number != user.policy_number:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only claim on your own policy")
    uploads = [(p.filename or "photo", await p.read()) for p in photos]

    svc, hub = _svc(request), _hub(request)
    claim_id = svc.new_claim_id()
    async with get_sessionmaker()() as session, session.begin():
        policy = await session.get(Policy, claim_form.policy_number)
        session.add(ClaimRecord(claim_id=claim_id, owner_id=user.id,
                                policy_number=claim_form.policy_number, status="processing",
                                review_reasons=[]))  # fmt: skip
    if policy:
        pii = (policy.holder_name, policy.holder_email, policy.holder_phone, policy.holder_address)
        hub.register_pii(claim_id, known_pii(*pii))
    hub.register_pii(claim_id, known_pii(user.name, user.email))
    # Claimants are recorded by user id (PII); staff by email (who decided matters for audit).
    hub.publish(
        Event(
            claim_id=claim_id,
            kind="status",
            name="submitted",
            actor=f"claimant:{user.id}",
            data={"photos": len(uploads)},
        )
    )
    _spawn(
        request,
        _execute(
            request,
            claim_id,
            svc.start(claim_form, uploads, claim_id, user_id=f"claimant:{user.id}"),
        ),
    )
    return {"claim_id": claim_id, "status": "submitted"}


@router.get("")
async def list_claims(
    user: Annotated[CurrentUser, Depends(current_user)],
    status_filter: str | None = None,
    assigned: Literal["me", "none"] | None = None,
) -> list[dict[str, Any]]:
    q = select(ClaimRecord).order_by(ClaimRecord.created_at.desc())
    if assigned == "me":
        q = q.where(ClaimRecord.assigned_to == user.email)
    elif assigned == "none":
        q = q.where(ClaimRecord.assigned_to.is_(None))
    if user.role is Role.CLAIMANT:
        q = q.where(ClaimRecord.owner_id == user.id)
    if status_filter:
        q = q.where(ClaimRecord.status == status_filter)
    async with get_sessionmaker()() as session:
        rows = (await session.scalars(q)).all()
    return [_summary(r, user) for r in rows]


@router.get("/{claim_id}")
async def get_claim(
    request: Request, claim_id: str, user: Annotated[CurrentUser, Depends(current_user)]
) -> dict[str, Any]:
    rec = await _record(claim_id, user)
    out = _summary(rec, user)
    try:
        run = await _svc(request).get(claim_id)
    except KeyError:
        return out | {"detail": None}
    if user.role is Role.CLAIMANT:
        # Claimant-safe view: decision letter only once the claim is closed.
        letter = run.letter if run.status is ClaimStatus.CLOSED else None
        errors = run.intake.errors if run.intake else []
        return out | {"letter": letter, "errors": errors}
    return out | {"detail": run.model_dump(mode="json")}


PHOTO_NAME = re.compile(r"^[0-9a-f]{16}\.(?:jpg|png)$")


def _photo_dir(claim_id: str) -> Path:
    return get_settings().photo_storage_dir / claim_id


@router.get("/{claim_id}/photos")
async def list_photos(
    claim_id: str, user: Annotated[CurrentUser, Depends(current_user)]
) -> list[str]:
    """File names of the claim's photos (owner claimant or staff)."""
    await _record(claim_id, user)
    d = _photo_dir(claim_id)
    return sorted(p.name for p in d.iterdir() if PHOTO_NAME.match(p.name)) if d.is_dir() else []


@router.get("/{claim_id}/photos/{name}")
async def get_photo(
    claim_id: str,
    name: str,
    user: Annotated[CurrentUser, Depends(current_user)],
    size: Literal["thumb", "full"] = "thumb",
) -> Response:
    """A re-encoded JPEG (EXIF/GPS stripped). Originals are never served."""
    await _record(claim_id, user)
    path = _photo_dir(claim_id) / name
    if not PHOTO_NAME.match(name) or not path.is_file():  # also blocks path traversal
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Photo not found")
    data = await asyncio.to_thread(display_jpeg, path, 320 if size == "thumb" else 1600)
    return Response(
        data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"}
    )


def _check_assignee(rec: ClaimRecord, user: CurrentUser) -> None:
    """Per-adjuster queue: only the assignee acts on an assigned claim."""
    if rec.assigned_to and rec.assigned_to != user.email:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Claim is assigned to {rec.assigned_to}")


async def _start_action(claim_id: str, user: CurrentUser) -> None:
    """Mark the claim processing; acting on an unassigned claim assigns it to the actor."""
    async with get_sessionmaker()() as session, session.begin():
        rec = await session.get(ClaimRecord, claim_id)
        rec.status = "processing"
        rec.assigned_to = rec.assigned_to or user.email


class Assignment(BaseModel):
    to: Literal["me"] | None = Field(description='"me" to take the claim, null to release it')


@router.post("/{claim_id}/assign")
async def assign_claim(
    request: Request,
    claim_id: str,
    body: Assignment,
    user: Annotated[CurrentUser, Depends(require(Role.ADJUSTER))],
) -> dict[str, Any]:
    rec = await _record(claim_id, user)
    _check_assignee(rec, user)  # can't take or release someone else's claim
    async with get_sessionmaker()() as session, session.begin():
        row = await session.get(ClaimRecord, claim_id)
        row.assigned_to = user.email if body.to == "me" else None
        assigned = row.assigned_to
    _hub(request).publish(Event(claim_id=claim_id, kind="human",
                                name="assigned" if assigned else "unassigned",
                                actor=user.email, data={"assigned_to": assigned}))  # fmt: skip
    return {"claim_id": claim_id, "assigned_to": assigned}


@router.post("/{claim_id}/review", status_code=status.HTTP_202_ACCEPTED)
async def review_claim(
    request: Request,
    claim_id: str,
    decision: ReviewDecision,
    user: Annotated[CurrentUser, Depends(require(Role.ADJUSTER))],
) -> dict[str, Any]:
    rec = await _record(claim_id, user)
    if rec.status != ClaimStatus.PENDING_REVIEW.value:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Claim is {rec.status}, not pending_review")
    _check_assignee(rec, user)
    decision = decision.model_copy(update={"adjuster": user.email})
    await _start_action(claim_id, user)
    _hub(request).publish(Event(claim_id=claim_id, kind="human", name=f"review_{decision.action}",
                                actor=user.email, data=decision.model_dump()))  # fmt: skip
    _spawn(
        request,
        _execute(
            request,
            claim_id,
            _svc(request).resume(claim_id, decision, user_id=f"claimant:{rec.owner_id}"),
        ),
    )
    return {"claim_id": claim_id, "status": "processing"}


@router.post("/{claim_id}/letter", status_code=status.HTTP_202_ACCEPTED)
async def edit_letter(
    request: Request,
    claim_id: str,
    edit: LetterEdit,
    user: Annotated[CurrentUser, Depends(require(Role.ADJUSTER))],
) -> dict[str, Any]:
    """Adjuster's corrected text for a letter that failed its checks."""
    rec = await _record(claim_id, user)
    if rec.status != ClaimStatus.PENDING_LETTER.value:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Claim is {rec.status}, not pending_letter")
    _check_assignee(rec, user)
    edit = edit.model_copy(update={"adjuster": user.email})
    await _start_action(claim_id, user)
    _hub(request).publish(Event(claim_id=claim_id, kind="human", name="letter_edited",
                                actor=user.email, data={"subject": edit.subject}))  # fmt: skip
    _spawn(
        request,
        _execute(
            request,
            claim_id,
            _svc(request).edit_letter(claim_id, edit, user_id=f"claimant:{rec.owner_id}"),
        ),
    )
    return {"claim_id": claim_id, "status": "processing"}


@router.get("/{claim_id}/audit")
async def audit_trail(
    claim_id: str, user: Annotated[CurrentUser, Depends(require(*STAFF))]
) -> list[dict[str, Any]]:
    await _record(claim_id, user)
    async with get_sessionmaker()() as session:
        rows = (await session.scalars(
            select(AuditEvent).where(AuditEvent.claim_id == claim_id)
            .order_by(AuditEvent.ts, AuditEvent.id)
        )).all()  # fmt: skip
    return [_audit_dict(r) for r in rows]


def _audit_dict(r: AuditEvent) -> dict[str, Any]:
    return {c: getattr(r, c) for c in ("ts", "kind", "name", "actor", "latency_ms",
            "input_tokens", "output_tokens", "cost_usd", "error", "data")}  # fmt: skip


@router.get("/{claim_id}/events")
async def stream_events(
    request: Request, claim_id: str, user: Annotated[CurrentUser, Depends(current_user)]
) -> EventSourceResponse:
    """SSE: node start/finish and status events. Claimants get step names only."""
    rec = await _record(claim_id, user)
    hub = _hub(request)
    queue = hub.subscribe(claim_id)

    def shape(e: Event) -> dict[str, str]:
        payload = e.model_dump(mode="json")
        if user.role is Role.CLAIMANT:
            payload = {"kind": e.kind, "name": e.name, "ts": payload["ts"]}
        return {"event": e.kind, "data": json.dumps(payload)}

    def visible(e: Event) -> bool:
        return not (user.role is Role.CLAIMANT and e.kind in ("llm", "tool"))

    async def gen():
        try:
            # 1) replay what already happened (from the audit table), 2) then stream live.
            await hub.flush()
            async with get_sessionmaker()() as session:
                rows = (await session.scalars(
                    select(AuditEvent).where(AuditEvent.claim_id == claim_id)
                    .order_by(AuditEvent.ts, AuditEvent.id)
                )).all()  # fmt: skip
            seen = set()
            for r in rows:
                e = Event(claim_id=claim_id, **_audit_dict(r))
                seen.add((e.ts, e.kind, e.name))
                if visible(e):
                    yield shape(e)
            if rec.status in TERMINAL and rows:
                return
            while not await request.is_disconnected():
                try:
                    e = await asyncio.wait_for(queue.get(), timeout=15)
                except TimeoutError:
                    continue  # sse-starlette sends keep-alive pings
                if (e.ts, e.kind, e.name) in seen or not visible(e):
                    continue
                yield shape(e)
                if e.kind == "status" and e.name in TERMINAL:
                    return
        finally:
            hub.unsubscribe(claim_id, queue)

    return EventSourceResponse(gen())


def reason_key(reason: str) -> str:
    """Group review reasons for the dashboard: drop per-claim detail, keep the check's name.

    flag:quote_not_verbatim:COLL-1 -> flag:quote_not_verbatim; fraud_score_0.77 -> fraud_score;
    low_confidence:cause and payout_at_or_above_5000 stay as they are (already categories).
    """
    if reason.startswith("flag:"):
        return ":".join(reason.split(":")[:2])
    if reason.startswith("fraud_score_"):
        return "fraud_score"
    return reason


@metrics_router.get("/metrics")
async def metrics(
    request: Request, user: Annotated[CurrentUser, Depends(require(*STAFF))]
) -> dict[str, Any]:
    """Impact dashboard numbers. Handling-time baseline is a stated assumption."""
    settings = request.app.state.settings
    async with get_sessionmaker()() as session:
        claims = (await session.scalars(select(ClaimRecord))).all()
        cost_rows = (await session.execute(
            select(AuditEvent.claim_id, func.sum(AuditEvent.cost_usd),
                   func.sum(AuditEvent.input_tokens), func.sum(AuditEvent.output_tokens))
            .where(AuditEvent.kind == "llm").group_by(AuditEvent.claim_id)
        )).all()  # fmt: skip
    by_status = Counter(c.status for c in claims)
    decided = [c for c in claims if c.decision]
    auto = [c for c in decided if c.decision == "auto_approved"]
    escalated = [c for c in claims if c.review_reasons]
    reasons = Counter(reason_key(r) for c in escalated for r in c.review_reasons)
    times = sorted(c.processing_ms for c in claims if c.processing_ms and c.status != "invalid")
    costs = [float(r[1] or 0) for r in cost_rows]
    return {
        "claims_total": len(claims),
        "by_status": dict(by_status),
        "decided": len(decided),
        "straight_through_rate": round(len(auto) / len(decided), 3) if decided else None,
        "escalation_rate": round(len(escalated) / len(claims), 3) if claims else None,
        "escalation_by_reason": dict(reasons.most_common()),
        "processing_ms_p50": round(statistics.median(times), 1) if times else None,
        "processing_ms_p95": round(times[int(0.95 * (len(times) - 1))], 1) if times else None,
        "llm_cost_per_claim_usd": round(sum(costs) / len(costs), 6) if costs else None,
        "tokens_per_claim": round(
            statistics.mean(int(r[2] or 0) + int(r[3] or 0) for r in cost_rows)
        )
        if cost_rows
        else None,  # fmt: skip
        "manual_handling_minutes_assumption": settings.manual_handling_minutes,
        "est_handling_hours_saved": round(len(auto) * settings.manual_handling_minutes / 60, 1),
    }
