"""Intake and triage subgraph.

    validate (code) --(errors)--> END        # invalid submissions never reach the LLM
                    \\-> extract (vision LLM) -> crosscheck (code) -> END

Policy problems that need judgment (cancelled, outside period) are flags, not errors: they
flow on so coverage marks them not_covered and a human decides (no automatic denials).
"""

from __future__ import annotations

from typing import Any, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from claimpilot.config import Settings, get_settings
from claimpilot.domain import ClaimExtraction, ClaimFacts, ClaimForm
from claimpilot.llm import LLMClient
from claimpilot.mcp_server import PolicyInfo
from claimpilot.photos import StoredPhoto, resized_jpeg_b64
from claimpilot.tools import ToolCallError, ToolClient

NODE = "intake_extract"
PROMPT_VERSION = "v2"
REQUIRED_FIELDS = ("incident_date", "damage_areas", "cause", "claim_type", "severity")

SYSTEM_PROMPT = """You are an auto-insurance intake specialist.
Extract structured facts from the claimant's form and the attached damage photos.

Rules:
- Use only what the form text states or the photos clearly show. Never invent details.
- damage_areas: specific parts (e.g. "rear bumper", "windshield", "left front door").
  Include parts visible as damaged in the photos even if the text omits them.
- claim_type: collision | theft | glass | weather | other.
- severity: rate the damage you can actually see on the vehicle, not how dramatic the scene
  looks. Use the lowest level whose description fits.
  * low: cosmetic or minor and limited to one area. Scratches, scuffs, paint damage, small
    dents, a chipped or cracked windshield. Panels are intact; the car is drivable.
  * medium: parts need repair or replacement but the body structure is intact. Crumpled or
    detached bumper, dented or creased panels, broken lights, grille or mirror, smashed or
    shattered glass, hail dents. Broken glass alone is not structural damage.
  * high: structural or extensive damage, or a likely total loss. Crushed front or rear
    section, deformed roof, pillars or cabin, displaced wheels, deployed airbags, fire damage,
    flood water inside the car, rollover, or a heavy object crushing the car.
- confidence: 0-1 per field. Use < 0.7 when the evidence is weak, missing or contradictory
  (e.g. photos do not show the damage described).
"""


class IntakeResult(BaseModel):
    errors: list[str] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    policy: PolicyInfo | None = None
    extraction: ClaimExtraction | None = None
    facts: ClaimFacts | None = None
    low_confidence_fields: list[str] = Field(default_factory=list)


class IntakeState(TypedDict, total=False):
    form: ClaimForm
    photos: list[StoredPhoto]
    upload_errors: list[str]
    result: IntakeResult


def render_messages(form: ClaimForm, photos: list[StoredPhoto], settings: Settings) -> list:
    content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                f"Claim form\n- Incident date: {form.incident_date.isoformat()}\n"
                f"- Location: {form.incident_location}\n"
                f"- Claimed amount: {form.claimed_amount or 'not stated'}\n"
                f"- Description: {form.description}\n\n"
                f"{len(photos)} damage photo(s) attached."
            ),
        }
    ]
    content += [
        {
            "type": "image",
            "base64": resized_jpeg_b64(p.path, settings.photo_max_side_px),
            "mime_type": "image/jpeg",
        }
        for p in photos
    ]
    return [SystemMessage(SYSTEM_PROMPT), HumanMessage(content=content)]


def build_intake_graph(llm: LLMClient, tools: ToolClient):
    settings = get_settings()

    async def validate(state: IntakeState) -> IntakeState:
        form, photos = state["form"], state.get("photos", [])
        errors = list(state.get("upload_errors", []))
        flags: list[str] = []
        if not 1 <= len(photos) <= settings.max_photos:
            errors.append(f"between 1 and {settings.max_photos} photos are required")
        policy = None
        try:
            policy = await tools.call("lookup_policy", PolicyInfo, policy_number=form.policy_number)
        except ToolCallError:
            errors.append(f"policy {form.policy_number} not found")
        if policy is not None:
            if policy.status != "active":
                flags.append(f"policy_{policy.status}")
            if not policy.effective_date <= form.incident_date <= policy.expiry_date:
                flags.append("incident_outside_policy_period")
        return {"result": IntakeResult(errors=errors, flags=flags, policy=policy)}

    def route(state: IntakeState) -> str:
        return END if state["result"].errors else "extract"

    async def extract(state: IntakeState) -> IntakeState:
        messages = render_messages(state["form"], state["photos"], settings)
        extraction = await llm.structured(NODE, ClaimExtraction, messages)
        return {"result": state["result"].model_copy(update={"extraction": extraction})}

    async def crosscheck(state: IntakeState) -> IntakeState:
        form, r = state["form"], state["result"]
        ex, flags = r.extraction, list(r.flags)
        if ex.incident_date and ex.incident_date != form.incident_date:
            flags.append("extracted_date_differs_from_form")
        seen = (ex.vehicle.make or "").lower()
        declared = r.policy.vehicle.get("make", "").lower()
        if seen and declared and seen not in declared and declared not in seen:
            flags.append("vehicle_make_mismatch")
        low = [
            f
            for f in REQUIRED_FIELDS
            if getattr(ex.confidence, f) < settings.extraction_confidence_min
        ]
        if not ex.damage_areas:
            flags.append("no_damage_areas_identified")
        facts = ClaimFacts(
            policy_number=form.policy_number,
            incident_date=form.incident_date,  # claimant-declared date is the record
            claim_type=ex.claim_type,
            description=form.description,
            damage_areas=ex.damage_areas,
            claimed_amount=form.claimed_amount,
        )
        return {
            "result": r.model_copy(
                update={"flags": flags, "facts": facts, "low_confidence_fields": low}
            )
        }

    g = StateGraph(IntakeState)
    g.add_node("validate", validate)
    g.add_node("extract", extract)
    g.add_node("crosscheck", crosscheck)
    g.add_edge(START, "validate")
    g.add_conditional_edges("validate", route, ["extract", END])
    g.add_edge("extract", "crosscheck")
    g.add_edge("crosscheck", END)
    return g.compile(name="intake")
