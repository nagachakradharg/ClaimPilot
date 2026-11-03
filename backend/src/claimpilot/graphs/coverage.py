"""Coverage verification subgraph.

    load_policy -> precheck --(outside period / cancelled)--> finalize
                           \\-> retrieve -> assess (LLM) -> validate -> finalize

Routing is deterministic (ADR-0001). The LLM only judges coverage from retrieved clauses;
citations are validated in code and deductible/limit come from the policy record.
"""

from __future__ import annotations

import re
from typing import Any, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from claimpilot.config import get_settings
from claimpilot.domain import (
    Citation,
    ClaimFacts,
    CoverageAssessment,
    CoverageDecision,
    CoveragePart,
    CoverageResult,
)
from claimpilot.llm import LLMClient
from claimpilot.models import Policy
from claimpilot.rag import Clause, get_clauses, retrieve_for_coverage

NODE = "coverage_assess"

SYSTEM_PROMPT = """You are a senior auto-insurance coverage analyst.
Decide whether the claim is covered under the policy, using ONLY the policy clauses provided.

Rules:
- decision = "covered" only if an insuring clause clearly applies AND no exclusion applies.
- decision = "not_covered" only if a clause clearly excludes or does not provide this coverage.
- decision = "ambiguous" if the facts are insufficient or clauses conflict. When unsure, choose
  "ambiguous" — a human adjuster reviews it.
- coverage_part: "collision" or "comprehensive" for physical damage to the insured vehicle;
  "none" if not covered.
- Cite every clause your decision relies on (insuring clause AND any exclusion considered
  decisive). Use clause ids exactly as shown in brackets, e.g. COLL-1.
- Each quote must be copied verbatim from that clause's text.
- Do not compute payouts, deductibles or limits.
"""


class CoverageState(TypedDict, total=False):
    facts: ClaimFacts
    policy: dict[str, Any] | None
    clauses: list[Clause]
    assessment: CoverageAssessment | None
    result: CoverageResult


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def build_query(facts: ClaimFacts) -> str:
    areas = ", ".join(facts.damage_areas)
    return f"{facts.claim_type.value} claim. {facts.description} Damage: {areas}"


def render_prompt(facts: ClaimFacts, policy: dict[str, Any], clauses: list[Clause]) -> list:
    clause_block = "\n\n".join(c.render() for c in clauses)
    claim = (
        f"Claim type (as reported): {facts.claim_type.value}\n"
        f"Incident date: {facts.incident_date.isoformat()}\n"
        f"Damage areas: {', '.join(facts.damage_areas) or 'not stated'}\n"
        f"Claimant description: {facts.description}"
    )
    header = f"Policy form: {policy['form_id']}"
    return [
        SystemMessage(SYSTEM_PROMPT),
        HumanMessage(f"{header}\n\n## Policy clauses\n{clause_block}\n\n## Claim\n{claim}"),
    ]


def build_coverage_graph(llm: LLMClient, sessionmaker: async_sessionmaker[AsyncSession]):
    settings = get_settings()

    async def load_policy(state: CoverageState) -> CoverageState:
        async with sessionmaker() as session:
            policy = await session.get(Policy, state["facts"].policy_number)
        if policy is None:
            return {"policy": None}
        return {
            "policy": {
                "form_id": policy.form_id,
                "status": policy.status,
                "effective_date": policy.effective_date,
                "expiry_date": policy.expiry_date,
                "vehicle_acv": policy.vehicle_acv,
                "collision_deductible": policy.collision_deductible,
                "comprehensive_deductible": policy.comprehensive_deductible,
            }
        }

    def precheck_route(state: CoverageState) -> str:
        policy, facts = state.get("policy"), state["facts"]
        if policy is None:
            return "finalize"
        in_period = policy["effective_date"] <= facts.incident_date <= policy["expiry_date"]
        return "retrieve" if in_period and policy["status"] == "active" else "finalize"

    async def retrieve(state: CoverageState) -> CoverageState:
        async with sessionmaker() as session:
            clauses = await retrieve_for_coverage(
                session,
                state["policy"]["form_id"],
                build_query(state["facts"]),
                settings.retrieval_top_k,
            )
        return {"clauses": clauses}

    async def assess(state: CoverageState) -> CoverageState:
        messages = render_prompt(state["facts"], state["policy"], state["clauses"])
        return {"assessment": await llm.structured(NODE, CoverageAssessment, messages)}

    async def finalize(state: CoverageState) -> CoverageState:
        return {"result": await _finalize(state, sessionmaker)}

    graph = StateGraph(CoverageState)
    graph.add_node("load_policy", load_policy)
    graph.add_node("retrieve", retrieve)
    graph.add_node("assess", assess)
    graph.add_node("finalize", finalize)
    graph.add_edge(START, "load_policy")
    graph.add_conditional_edges("load_policy", precheck_route, ["retrieve", "finalize"])
    graph.add_edge("retrieve", "assess")
    graph.add_edge("assess", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile(name="coverage")


async def _finalize(
    state: CoverageState, sessionmaker: async_sessionmaker[AsyncSession]
) -> CoverageResult:
    """Deterministic validation and deductible/limit lookup."""
    facts, policy = state["facts"], state.get("policy")

    if policy is None:
        return CoverageResult(
            decision=CoverageDecision.AMBIGUOUS, coverage_part=CoveragePart.NONE,
            deductible=None, limit=None, citations=[], retrieved_clause_ids=[],
            reasoning="Policy not found.", flags=["policy_not_found"],
        )  # fmt: skip

    if "assessment" not in state:  # precheck failed: cancelled or outside period
        async with sessionmaker() as session:
            gen1 = await get_clauses(session, policy["form_id"], ["GEN-1"])
        flag = "policy_cancelled" if policy["status"] != "active" else "outside_policy_period"
        return CoverageResult(
            decision=CoverageDecision.NOT_COVERED,
            coverage_part=CoveragePart.NONE,
            deductible=None,
            limit=None,
            citations=[Citation(clause_id="GEN-1", quote=gen1[0].text.split(". ")[0] + ".")]
            if gen1
            else [],
            retrieved_clause_ids=["GEN-1"] if gen1 else [],
            reasoning=(
                f"The incident date {facts.incident_date} is not covered: the policy is "
                f"{policy['status']} with period {policy['effective_date']} to "
                f"{policy['expiry_date']}."
            ),
            flags=[flag],
        )

    a: CoverageAssessment = state["assessment"]
    retrieved = {c.clause_id: c for c in state["clauses"]}
    flags: list[str] = []
    decision, part = a.decision, a.coverage_part

    valid_citations = []
    for cit in a.citations:
        clause = retrieved.get(cit.clause_id)
        if clause is None:
            flags.append(f"invalid_citation:{cit.clause_id}")
            continue
        if _norm(cit.quote) not in _norm(clause.text):
            flags.append(f"quote_not_verbatim:{cit.clause_id}")
        valid_citations.append(cit)

    if any(f.startswith("invalid_citation") for f in flags) or not valid_citations:
        decision = CoverageDecision.AMBIGUOUS
        flags.append("citation_validation_failed")

    deductible = None
    if decision is CoverageDecision.COVERED:
        deductible = {
            CoveragePart.COLLISION: policy["collision_deductible"],
            CoveragePart.COMPREHENSIVE: policy["comprehensive_deductible"],
        }.get(part)
        if deductible is None:  # e.g. "covered" on a liability-only form, or part = none
            decision = CoverageDecision.AMBIGUOUS
            flags.append("coverage_part_not_on_policy")

    return CoverageResult(
        decision=decision,
        coverage_part=part if decision is CoverageDecision.COVERED else CoveragePart.NONE,
        deductible=deductible,
        limit=policy["vehicle_acv"] if decision is CoverageDecision.COVERED else None,
        citations=valid_citations,
        reasoning=a.reasoning,
        retrieved_clause_ids=list(retrieved),
        flags=flags,
    )


async def run_coverage(
    facts: ClaimFacts, llm: LLMClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> CoverageResult:
    graph = build_coverage_graph(llm, sessionmaker)
    out = await graph.ainvoke({"facts": facts})
    return out["result"]
