"""Offline tests for the cost-aware economy calculation layer."""
from decimal import Decimal

from economics import (
    AllocationPolicy,
    EconomyPolicyError,
    RevenueSummary,
    UnitEconomics,
    break_even_customers,
    stars_settlement_value,
    validate_launch_economics,
)


def test_unit_economics_reports_positive_surplus_and_margin():
    unit = UnitEconomics(
        gross_revenue="10",
        payment_cost="1",
        channel_reward="4",
        refund_reserve="0.75",
        fraud_reserve="0.50",
        operating_cost="1",
        tax_reserve="0.75",
    )
    assert unit.total_costs == Decimal("8.00")
    assert unit.platform_surplus == Decimal("2.00")
    assert unit.contribution_margin == Decimal("0.2000")
    assert unit.viable


def test_allocation_policy_cannot_overpromise():
    policy = AllocationPolicy("0.40", "0.15", "0.10", "0.15", "0.10")
    unit = policy.apply("10", payment_cost="1")
    assert unit.channel_reward == Decimal("4.00")
    assert unit.refund_reserve == Decimal("1.50")
    assert unit.fraud_reserve == Decimal("1.00")
    # The configured percentages consume 90% of gross revenue and payment
    # cost consumes the remaining 10%; there is no unallocated surplus.
    assert unit.platform_surplus == Decimal("0.00")

    try:
        AllocationPolicy("0.60", "0.20", "0.20", "0.10", "0.01")
    except EconomyPolicyError:
        pass
    else:
        raise AssertionError("over-allocated policy was accepted")


def test_break_even_uses_ceiling_not_float_rounding():
    assert break_even_customers("100", "2") == 50
    assert break_even_customers("101", "2") == 51


def test_stars_value_is_explicit_and_configurable():
    assert stars_settlement_value(1000) == Decimal("13.00")
    assert stars_settlement_value(1000, "0.012") == Decimal("12.00")


def test_launch_validation_fails_without_profit_or_reserve():
    losing = RevenueSummary(
        gross_revenue="100",
        payment_costs="10",
        channel_rewards="80",
        refund_reserves="10",
        fraud_reserves="5",
        operating_costs="5",
        tax_reserves="2",
        fixed_costs="5",
        required_reserve="1",
    )
    try:
        validate_launch_economics(losing)
    except EconomyPolicyError:
        pass
    else:
        raise AssertionError("loss-making launch was accepted")


def test_owner_surplus_protects_required_reserve():
    summary = RevenueSummary(
        gross_revenue="1000",
        payment_costs="50",
        channel_rewards="350",
        refund_reserves="100",
        fraud_reserves="50",
        operating_costs="100",
        tax_reserves="100",
        fixed_costs="100",
        required_reserve="100",
    )
    assert summary.operating_surplus == Decimal("150.00")
    assert summary.owner_distributable_surplus == Decimal("50.00")
    validate_launch_economics(summary, minimum_margin="0.20")


if __name__ == "__main__":
    tests = [
        test_unit_economics_reports_positive_surplus_and_margin,
        test_allocation_policy_cannot_overpromise,
        test_break_even_uses_ceiling_not_float_rounding,
        test_stars_value_is_explicit_and_configurable,
        test_launch_validation_fails_without_profit_or_reserve,
        test_owner_surplus_protects_required_reserve,
    ]
    for test_case in tests:
        test_case()
        print(f"PASS {test_case.__name__}")
