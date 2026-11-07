"""Domain schemas shared across graph nodes, API and evals."""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, Field


class ClaimType(StrEnum):
    COLLISION = "collision"
    THEFT = "theft"
    GLASS = "glass"
    WEATHER = "weather"
    OTHER = "other"


class ClaimFacts(BaseModel):
    """Facts coverage needs. Produced by intake (week 2); hand-written in evals for now."""

    policy_number: str
    incident_date: date
    claim_type: ClaimType
    description: str = Field(description="Claimant's account of what happened")
    damage_areas: list[str] = Field(default_factory=list)
    claimed_amount: float | None = None


class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ClaimForm(BaseModel):
    """What the claimant submits. Declared values; photos travel separately."""

    policy_number: str = Field(min_length=3, max_length=32)
    incident_date: date
    incident_location: str = Field(min_length=2, max_length=300)
    description: str = Field(min_length=10, max_length=4000)
    claimed_amount: float | None = Field(default=None, ge=0)


class ExtractionConfidence(BaseModel):
    incident_date: float = Field(ge=0, le=1)
    location: float = Field(ge=0, le=1)
    vehicle: float = Field(ge=0, le=1)
    damage_areas: float = Field(ge=0, le=1)
    cause: float = Field(ge=0, le=1)
    claim_type: float = Field(ge=0, le=1)
    severity: float = Field(ge=0, le=1)


class VehicleSeen(BaseModel):
    make: str | None = None
    model: str | None = None
    color: str | None = None


class ClaimExtraction(BaseModel):
    """LLM output from form + photos."""

    incident_date: date | None = Field(description="Date of loss as stated by the claimant")
    location: str | None
    vehicle: VehicleSeen = Field(description="Vehicle as visible in photos / described")
    damage_areas: list[str] = Field(
        description="Damaged parts visible or described, e.g. 'rear bumper', 'windshield'"
    )
    described_cause: str = Field(description="One sentence: what caused the damage")
    claimed_amount: float | None
    claim_type: ClaimType
    severity: Severity = Field(description="low: cosmetic; medium: panels/parts; high: structural")
    confidence: ExtractionConfidence = Field(description="0-1 per field; low if not supported")


class CoverageDecision(StrEnum):
    COVERED = "covered"
    NOT_COVERED = "not_covered"
    AMBIGUOUS = "ambiguous"


class CoveragePart(StrEnum):
    COLLISION = "collision"
    COMPREHENSIVE = "comprehensive"
    NONE = "none"


class Citation(BaseModel):
    clause_id: str
    quote: str = Field(description="Exact sentence(s) from the clause that support the decision")


class CoverageAssessment(BaseModel):
    """What the LLM returns. Deductible/limit are NOT asked of the model (computed in code).
    `reasoning` comes first so models without native thinking reason before deciding."""

    reasoning: str = Field(
        description="Steps 1-4: cause of loss, insuring clause, exclusions, conditions"
    )
    decision: CoverageDecision
    coverage_part: CoveragePart = Field(
        description="Which physical-damage coverage applies; 'none' if not covered"
    )
    citations: list[Citation] = Field(
        description="Clauses that support the decision, using clause ids from the context only"
    )


class CoverageResult(BaseModel):
    """Final, validated output of the coverage subgraph."""

    decision: CoverageDecision
    coverage_part: CoveragePart
    deductible: float | None
    limit: float | None
    citations: list[Citation]
    reasoning: str
    retrieved_clause_ids: list[str]
    flags: list[str] = Field(default_factory=list)  # e.g. "invalid_citation", "outside_period"
