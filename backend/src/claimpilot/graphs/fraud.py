"""Fraud investigation subgraph.

    START -> doc_consistency (LLM) --\\
          -> claim_history (rules) ----> synthesize (code) -> END
          -> photo_match (vision LLM) -/

The three checks run concurrently (fan-out/fan-in); each records start/finish timestamps so
the overlap is visible in the trace. Synthesis is deterministic and auditable. A fraud score
never denies a claim: it can only add a review reason at the gate.
"""

from __future__ import annotations

import operator
from datetime import UTC, datetime
from typing import Annotated, Any, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from claimpilot.config import get_settings
from claimpilot.domain import ClaimExtraction, ClaimForm, ClaimType
from claimpilot.llm import LLMClient
from claimpilot.mcp_server import ClaimHistory, PolicyInfo
from claimpilot.photos import StoredPhoto, resized_jpeg_b64
from claimpilot.tools import ToolClient

DOC_NODE, PHOTO_NODE = "fraud_doc_consistency", "fraud_photo_match"
WEIGHTS = {"doc_consistency": 0.30, "claim_history": 0.35, "photo_match": 0.35}

DOC_PROMPT = """You check an auto-insurance claim for internal consistency.
Compare the claimant's form, the facts extracted from it and the policy details.
Flag only concrete contradictions or implausibilities you can point to, for example:
dates that disagree, an amount far out of line with the described damage, a vehicle that
does not match the policy, or a story that contradicts itself.
Do not speculate about intent. Missing detail alone is not a contradiction.
risk: 0 = fully consistent, 1 = clear contradictions. Most honest claims are below 0.2.
"""

PHOTO_PROMPT = """You compare damage photos with the claimant's description of an incident.
Does the visible damage match the story: the location on the vehicle (front, rear, side,
roof, glass), the type of damage (impact, hail, fire, water, scratches) and its extent?
List what you see. Flag mismatches only when the photos clearly contradict the description,
for example: no visible damage, damage on a different side, or a different kind of damage.
risk: 0 = photos match the story, 1 = photos clearly contradict it.
"""


class CheckResult(BaseModel):
    name: str
    risk: float = Field(ge=0, le=1)
    findings: list[str]
    started_at: datetime
    finished_at: datetime


class DocConsistency(BaseModel):
    issues: list[str] = Field(description="Concrete contradictions found; empty if none")
    risk: float = Field(ge=0, le=1)


class PhotoMatch(BaseModel):
    observations: list[str] = Field(description="What the photos show, briefly")
    mismatches: list[str] = Field(description="Clear contradictions with the description")
    risk: float = Field(ge=0, le=1)


class FraudResult(BaseModel):
    score: float = Field(ge=0, le=1)
    reasons: list[str]  # internal only; never shown to the claimant
    checks: list[CheckResult]


class FraudState(TypedDict, total=False):
    form: ClaimForm
    extraction: ClaimExtraction
    policy: PolicyInfo
    photos: list[StoredPhoto]
    checks: Annotated[list[CheckResult], operator.add]  # fan-in: each branch appends
    result: FraudResult


def _now() -> datetime:
    return datetime.now(UTC)


def history_risk(h: ClaimHistory, claim_type: ClaimType) -> tuple[float, list[str]]:
    """Rules over claim-history signals. Each rule adds risk and a plain reason."""
    risk, why = 0.0, []
    if h.past_claims >= 3:
        risk += 0.35
        why.append(f"{h.past_claims} prior claims on this policy")
    elif h.past_claims >= 1:
        risk += 0.10
        why.append(f"{h.past_claims} prior claim(s) on this policy")
    if h.days_policy_to_accident <= 7:
        risk += 0.35
        why.append(f"loss {h.days_policy_to_accident} days after the policy started")
    elif h.days_policy_to_accident <= 30:
        risk += 0.15
        why.append(f"loss {h.days_policy_to_accident} days after the policy started")
    if h.address_change_recent:
        risk += 0.20
        why.append("address changed within the last 6 months")
    if claim_type is ClaimType.THEFT and not h.police_report_filed:
        risk += 0.25
        why.append("theft claim without a police report on file")
    return min(risk, 1.0), why


def synthesize_score(checks: list[CheckResult]) -> float:
    """Weighted mean, floored at 75% of the strongest single signal, so one clear red flag
    (risk >= 0.8) alone is enough to reach the default review threshold of 0.6."""
    if not checks:
        return 0.0
    weighted = sum(WEIGHTS[c.name] * c.risk for c in checks) / sum(WEIGHTS[c.name] for c in checks)
    return round(min(1.0, max(weighted, 0.75 * max(c.risk for c in checks))), 2)


def build_fraud_graph(llm: LLMClient, tools: ToolClient):
    settings = get_settings()

    async def doc_consistency(state: FraudState) -> dict[str, Any]:
        start = _now()
        f, ex, p = state["form"], state["extraction"], state["policy"]
        text = (
            f"Policy: {p.policy_number}, {p.status}, period {p.effective_date} to "
            f"{p.expiry_date}, vehicle {p.vehicle.get('year')} {p.vehicle.get('make')} "
            f"{p.vehicle.get('model')}, ACV ${p.vehicle_acv:,.0f}\n"
            f"Form: date {f.incident_date}, location {f.incident_location}, claimed "
            f"{f.claimed_amount or 'not stated'}\nDescription: {f.description}\n"
            f"Extracted: date {ex.incident_date}, cause '{ex.described_cause}', type "
            f"{ex.claim_type.value}, severity {ex.severity.value}, areas {ex.damage_areas}, "
            f"vehicle seen {ex.vehicle.make or '?'} {ex.vehicle.model or ''}"
        )
        out = await llm.structured(
            DOC_NODE, DocConsistency, [SystemMessage(DOC_PROMPT), HumanMessage(text)]
        )
        return {"checks": [CheckResult(name="doc_consistency", risk=out.risk,
                                       findings=out.issues, started_at=start,
                                       finished_at=_now())]}  # fmt: skip

    async def claim_history(state: FraudState) -> dict[str, Any]:
        start = _now()
        h = await tools.call(
            "get_claim_history", ClaimHistory, policy_number=state["form"].policy_number
        )
        risk, why = history_risk(h, state["extraction"].claim_type)
        return {"checks": [CheckResult(name="claim_history", risk=risk, findings=why,
                                       started_at=start, finished_at=_now())]}  # fmt: skip

    async def photo_match(state: FraudState) -> dict[str, Any]:
        start = _now()
        content: list[dict[str, Any]] = [
            {"type": "text", "text": f"Claimant's description: {state['form'].description}"}
        ]
        content += [
            {
                "type": "image",
                "base64": resized_jpeg_b64(p.path, settings.photo_max_side_px),
                "mime_type": "image/jpeg",
            }
            for p in state["photos"]
        ]
        out = await llm.structured(
            PHOTO_NODE, PhotoMatch, [SystemMessage(PHOTO_PROMPT), HumanMessage(content=content)]
        )
        return {"checks": [CheckResult(name="photo_match", risk=out.risk,
                                       findings=out.mismatches, started_at=start,
                                       finished_at=_now())]}  # fmt: skip

    async def synthesize(state: FraudState) -> dict[str, Any]:
        checks = sorted(state["checks"], key=lambda c: c.name)
        reasons = [f"{c.name}: {f}" for c in checks if c.risk >= 0.3 for f in c.findings]
        return {"result": FraudResult(score=synthesize_score(checks), reasons=reasons,
                                      checks=checks)}  # fmt: skip

    g = StateGraph(FraudState)
    for name, fn in [
        ("doc_consistency", doc_consistency),
        ("claim_history", claim_history),
        ("photo_match", photo_match),
    ]:
        g.add_node(name, fn)
        g.add_edge(START, name)
        g.add_edge(name, "synthesize")
    g.add_node("synthesize", synthesize)
    g.add_edge("synthesize", END)
    return g.compile(name="fraud")
