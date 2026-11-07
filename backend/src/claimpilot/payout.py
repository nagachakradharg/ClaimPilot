"""Deterministic payout math. The LLM never produces the number.

Policy basis (PAP-STD-01): LIM-1 (lesser of ACV and repair cost), LIM-2 (total loss at 75% of
ACV), DED-1 (deductible per loss), COMP-2 (windshield chip repair: no deductible).
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from pydantic import BaseModel

CENT = Decimal("0.01")


class PayoutBasis(StrEnum):
    REPAIR = "repair"
    TOTAL_LOSS = "total_loss"
    BELOW_DEDUCTIBLE = "below_deductible"


class PayoutStep(BaseModel):
    label: str
    amount: float
    clause_id: str | None = None
    note: str | None = None


class PayoutResult(BaseModel):
    basis: PayoutBasis
    repair_estimate: float
    vehicle_acv: float
    deductible_applied: float
    payout: float
    total_loss: bool
    steps: list[PayoutStep]


def _d(x: float) -> Decimal:
    return Decimal(str(x)).quantize(CENT, rounding=ROUND_HALF_UP)


def compute_payout(
    repair_estimate: float,
    deductible: float,
    vehicle_acv: float,
    *,
    total_loss_threshold: float = 0.75,
    waive_deductible: bool = False,
) -> PayoutResult:
    if min(repair_estimate, deductible, vehicle_acv) < 0:
        raise ValueError("amounts must be non-negative")
    if vehicle_acv == 0:
        raise ValueError("vehicle ACV must be positive")

    estimate, ded, acv = _d(repair_estimate), _d(deductible), _d(vehicle_acv)
    steps = [
        PayoutStep(label="Repair estimate (lowest shop quote)", amount=float(estimate)),
        PayoutStep(label="Vehicle actual cash value", amount=float(acv)),
    ]

    threshold = (acv * Decimal(str(total_loss_threshold))).quantize(CENT)
    total_loss = estimate >= threshold
    if total_loss:
        base = acv
        steps.append(
            PayoutStep(
                label="Total loss: repair cost ≥ threshold, settle at ACV",
                amount=float(base),
                clause_id="LIM-2",
                note=f"threshold {total_loss_threshold:.0%} of ACV = {threshold}",
            )
        )
    else:
        base = min(estimate, acv)
        steps.append(
            PayoutStep(
                label="Covered amount: lesser of repair cost and ACV",
                amount=float(base),
                clause_id="LIM-1",
            )
        )

    applied = Decimal("0.00") if waive_deductible else ded
    steps.append(
        PayoutStep(
            label="Deductible" + (" (waived: windshield chip repair)" if waive_deductible else ""),
            amount=-float(applied),
            clause_id="COMP-2" if waive_deductible else "DED-1",
        )
    )

    payout = max(base - applied, Decimal("0.00"))
    if payout == 0:
        basis = PayoutBasis.BELOW_DEDUCTIBLE
    else:
        basis = PayoutBasis.TOTAL_LOSS if total_loss else PayoutBasis.REPAIR
    steps.append(PayoutStep(label="Recommended payout", amount=float(payout)))

    return PayoutResult(
        basis=basis,
        repair_estimate=float(estimate),
        vehicle_acv=float(acv),
        deductible_applied=float(applied),
        payout=float(payout),
        total_loss=total_loss,
        steps=steps,
    )
