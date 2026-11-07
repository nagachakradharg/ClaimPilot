import pytest

from claimpilot.estimates import estimate_repairs
from claimpilot.payout import PayoutBasis, compute_payout


def test_standard_repair():
    r = compute_payout(2181.60, 400, 46100)
    assert r.basis is PayoutBasis.REPAIR
    assert r.payout == 1781.60
    assert [s.clause_id for s in r.steps if s.clause_id] == ["LIM-1", "DED-1"]
    assert r.steps[-1].amount == r.payout


def test_below_deductible_pays_zero():
    r = compute_payout(350, 500, 20000)
    assert r.payout == 0 and r.basis is PayoutBasis.BELOW_DEDUCTIBLE


def test_exactly_deductible_pays_zero():
    assert compute_payout(500, 500, 20000).payout == 0


def test_total_loss_at_threshold_boundary():
    r = compute_payout(7500, 500, 10000)  # exactly 75% of ACV
    assert r.total_loss and r.basis is PayoutBasis.TOTAL_LOSS
    assert r.payout == 9500  # ACV - deductible, not estimate - deductible
    assert any(s.clause_id == "LIM-2" for s in r.steps)


def test_just_below_total_loss_threshold():
    r = compute_payout(7499.99, 500, 10000)
    assert not r.total_loss and r.payout == 6999.99


def test_estimate_above_acv_is_capped_at_acv():  # "above limit"
    r = compute_payout(25000, 1000, 12000)
    assert r.total_loss and r.payout == 11000


def test_chip_repair_waives_deductible():
    r = compute_payout(71, 250, 15000, waive_deductible=True)
    assert r.payout == 71 and r.deductible_applied == 0
    assert any(s.clause_id == "COMP-2" for s in r.steps)


def test_cent_rounding_is_exact():
    assert compute_payout(1000.005, 0.0, 5000).payout == 1000.01


@pytest.mark.parametrize("args", [(-1, 0, 100), (100, -1, 100), (100, 0, 0)])
def test_rejects_invalid_amounts(args):
    with pytest.raises(ValueError):
        compute_payout(*args)


def test_estimates_take_lowest_quote_and_report_unpriced():
    e = estimate_repairs(["rear bumper", "spoiler"], "medium", "Sedan", "Honda")
    assert e.lowest_total == min(q.total for q in e.quotes) > 0
    assert e.unpriced_areas == ["spoiler"]
    assert not e.chip_repair_only


def test_estimates_scale_with_severity_class_and_luxury():
    base = estimate_repairs(["hood"], "medium", "Sedan", "Honda").lowest_total
    assert estimate_repairs(["hood"], "high", "Sedan", "Honda").lowest_total > base
    assert estimate_repairs(["hood"], "medium", "Sport", "Honda").lowest_total > base
    assert estimate_repairs(["hood"], "medium", "Sedan", "BMW").lowest_total > base


def test_chip_only_detection():
    assert estimate_repairs(["windshield chip"], "low", "Sedan", "Honda").chip_repair_only
