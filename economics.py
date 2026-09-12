"""Cost-aware CLICKMINT economy calculations.

This module deliberately does not process payments, create deposits, credit Boost,
or execute withdrawals.  It is the policy/calculation layer that must be reviewed
and tested before those features are enabled.

All money-like values are Decimal and must be supplied in one accounting currency.
Telegram Stars are converted using an explicitly supplied, configurable settlement
value; the value is never inferred from a user's local purchase price.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN

D = Decimal
ZERO = D("0")
ONE = D("1")
CENT = D("0.01")


class EconomyPolicyError(ValueError):
    """Raised when a revenue policy is unsafe or internally inconsistent."""


def money(value: Decimal | int | float | str) -> Decimal:
    """Convert a value to a two-decimal accounting amount."""
    try:
        result = D(str(value)).quantize(CENT)
    except Exception as exc:  # pragma: no cover - defensive boundary
        raise EconomyPolicyError(f"invalid monetary value: {value!r}") from exc
    if not result.is_finite():
        raise EconomyPolicyError("monetary value must be finite")
    return result


def ratio(value: Decimal | int | float | str) -> Decimal:
    """Convert a percentage ratio and require 0 <= value <= 1."""
    try:
        result = D(str(value))
    except Exception as exc:  # pragma: no cover
        raise EconomyPolicyError(f"invalid ratio: {value!r}") from exc
    if not result.is_finite() or result < ZERO or result > ONE:
        raise EconomyPolicyError("ratios must be between 0 and 1")
    return result


@dataclass(frozen=True)
class UnitEconomics:
    """One realized Boost/payment unit and its resulting platform economics."""

    gross_revenue: Decimal
    payment_cost: Decimal = ZERO
    channel_reward: Decimal = ZERO
    refund_reserve: Decimal = ZERO
    fraud_reserve: Decimal = ZERO
    operating_cost: Decimal = ZERO
    tax_reserve: Decimal = ZERO

    def __post_init__(self):
        fields = (
            "gross_revenue", "payment_cost", "channel_reward",
            "refund_reserve", "fraud_reserve", "operating_cost", "tax_reserve",
        )
        for field in fields:
            value = money(getattr(self, field))
            if value < ZERO:
                raise EconomyPolicyError(f"{field} cannot be negative")
            object.__setattr__(self, field, value)

    @property
    def total_costs(self) -> Decimal:
        return sum(
            (self.payment_cost, self.channel_reward, self.refund_reserve,
             self.fraud_reserve, self.operating_cost, self.tax_reserve),
            ZERO,
        ).quantize(CENT)

    @property
    def platform_surplus(self) -> Decimal:
        """Surplus after direct costs and all configured reserves."""
        return (self.gross_revenue - self.total_costs).quantize(CENT)

    @property
    def contribution_margin(self) -> Decimal:
        if self.gross_revenue == ZERO:
            return ZERO
        return (self.platform_surplus / self.gross_revenue).quantize(D("0.0001"))

    @property
    def viable(self) -> bool:
        return self.platform_surplus > ZERO


@dataclass(frozen=True)
class AllocationPolicy:
    """Allocation of realized revenue before owner surplus is calculated.

    Percentages apply to realized gross revenue.  The remainder is the platform's
    pre-fixed-cost surplus.  This is intentionally explicit so a policy cannot
    silently promise more channel rewards than a payment can fund.
    """

    channel_reward_pct: Decimal
    refund_reserve_pct: Decimal
    fraud_reserve_pct: Decimal
    operating_pct: Decimal
    tax_pct: Decimal

    def __post_init__(self):
        values = {
            "channel_reward_pct": self.channel_reward_pct,
            "refund_reserve_pct": self.refund_reserve_pct,
            "fraud_reserve_pct": self.fraud_reserve_pct,
            "operating_pct": self.operating_pct,
            "tax_pct": self.tax_pct,
        }
        for name, value in list(values.items()):
            normalized = ratio(value)
            object.__setattr__(self, name, normalized)
            values[name] = normalized
        if sum(values.values(), ZERO) > ONE:
            raise EconomyPolicyError("allocation percentages cannot exceed 100%")

    def apply(self, gross_revenue: Decimal | int | float | str,
              payment_cost: Decimal | int | float | str = ZERO) -> UnitEconomics:
        gross = money(gross_revenue)
        payment = money(payment_cost)
        return UnitEconomics(
            gross_revenue=gross,
            payment_cost=payment,
            channel_reward=money(gross * self.channel_reward_pct),
            refund_reserve=money(gross * self.refund_reserve_pct),
            fraud_reserve=money(gross * self.fraud_reserve_pct),
            operating_cost=money(gross * self.operating_pct),
            tax_reserve=money(gross * self.tax_pct),
        )


@dataclass(frozen=True)
class RevenueSummary:
    """Period-level economics used by the future Admin dashboard."""

    gross_revenue: Decimal
    payment_costs: Decimal
    channel_rewards: Decimal
    refund_reserves: Decimal
    fraud_reserves: Decimal
    operating_costs: Decimal
    tax_reserves: Decimal
    fixed_costs: Decimal = ZERO
    required_reserve: Decimal = ZERO

    def __post_init__(self):
        for field in (
            "gross_revenue", "payment_costs", "channel_rewards",
            "refund_reserves", "fraud_reserves", "operating_costs",
            "tax_reserves", "fixed_costs", "required_reserve",
        ):
            value = money(getattr(self, field))
            if value < ZERO:
                raise EconomyPolicyError(f"{field} cannot be negative")
            object.__setattr__(self, field, value)

    @property
    def variable_costs(self) -> Decimal:
        return sum((self.payment_costs, self.channel_rewards,
                    self.refund_reserves, self.fraud_reserves,
                    self.operating_costs, self.tax_reserves), ZERO).quantize(CENT)

    @property
    def operating_surplus(self) -> Decimal:
        return (self.gross_revenue - self.variable_costs - self.fixed_costs).quantize(CENT)

    @property
    def owner_distributable_surplus(self) -> Decimal:
        """Surplus remaining after the required reserve target is protected."""
        return max(ZERO, self.operating_surplus - self.required_reserve).quantize(CENT)

    @property
    def contribution_margin(self) -> Decimal:
        if self.gross_revenue == ZERO:
            return ZERO
        return ((self.gross_revenue - self.variable_costs) /
                self.gross_revenue).quantize(D("0.0001"))


def break_even_customers(monthly_fixed_costs: Decimal | int | float | str,
                         contribution_per_customer: Decimal | int | float | str) -> int:
    """Return the smallest number of equivalent customers needed to cover fixed costs."""
    fixed = money(monthly_fixed_costs)
    contribution = money(contribution_per_customer)
    if fixed < ZERO or contribution <= ZERO:
        raise EconomyPolicyError("fixed costs must be non-negative and contribution must be positive")
    if fixed == ZERO:
        return 0
    # Decimal ceiling without floating-point rounding.
    return int((fixed / contribution).to_integral_value(rounding=ROUND_DOWN)) + (
        0 if fixed % contribution == ZERO else 1
    )


def stars_settlement_value(stars: int,
                           value_per_star: Decimal | int | float | str = "0.013") -> Decimal:
    """Estimate realized value from Stars using an explicit policy value.

    The default is a configurable reference value, not a guarantee.  The caller
    must still apply holds, refunds, regional availability, and provider rules.
    """
    if int(stars) < 0:
        raise EconomyPolicyError("Stars cannot be negative")
    try:
        value = D(str(value_per_star))
    except Exception as exc:
        raise EconomyPolicyError("invalid value per Star") from exc
    if not value.is_finite() or value < ZERO:
        raise EconomyPolicyError("value per Star cannot be negative")
    return money(int(stars) * value)


def validate_launch_economics(summary: RevenueSummary,
                              minimum_margin: Decimal | int | float | str = "0.20") -> None:
    """Fail closed when a planned launch is not profitable and adequately reserved."""
    target = ratio(minimum_margin)
    if summary.operating_surplus <= ZERO:
        raise EconomyPolicyError("planned period is not operating-surplus positive")
    if summary.contribution_margin < target:
        raise EconomyPolicyError(
            f"contribution margin {summary.contribution_margin} is below target {target}"
        )
    if summary.owner_distributable_surplus <= ZERO:
        raise EconomyPolicyError("required reserve would consume all distributable surplus")
