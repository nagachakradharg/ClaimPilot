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
    """What the LLM returns. Deductible/limit are NOT asked of the model (computed in code)."""

    decision: CoverageDecision
    coverage_part: CoveragePart = Field(
        description="Which physical-damage coverage applies; 'none' if not covered"
    )
    citations: list[Citation] = Field(
        description="Clauses that support the decision, using clause ids from the context only"
    )
    reasoning: str = Field(description="2-4 sentences, plain language")


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
