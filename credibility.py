"""Credibility and performance policy primitives.

This module is intentionally independent of Telegram and persistence.  It provides
stable, explainable calculations for the later marketplace/analytics services.
Scores are destination-oriented because one user may manage multiple channels.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from math import exp


class CredibilityPolicyError(ValueError):
    pass


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def smoothed_rate(successes: int, trials: int, *, prior_mean: float = 0.5,
                  prior_strength: int = 5) -> float:
    """Bayesian-style rate that prevents tiny samples dominating the score."""
    if successes < 0 or trials < 0 or successes > trials:
        raise CredibilityPolicyError("successes/trials are inconsistent")
    if prior_strength < 0 or not 0 <= prior_mean <= 1:
        raise CredibilityPolicyError("invalid prior")
    denominator = trials + prior_strength
    if denominator == 0:
        return 0.0
    return (successes + prior_strength * prior_mean) / denominator


def cohort_percentile(value: float, cohort_values: list[float]) -> float:
    """Return a deterministic percentile in [0, 1].

    Values at or below the minimum receive a small but non-zero score.  A single
    observation is treated as the median rather than as a fake leaderboard.
    """
    if not cohort_values:
        return 0.5
    values = sorted(float(v) for v in cohort_values)
    if len(values) == 1:
        return 0.5
    less_or_equal = sum(1 for item in values if item <= float(value))
    return _clamp((less_or_equal - 1) / (len(values) - 1))


def decay_weight(age_days: float, half_life_days: float = 30.0) -> float:
    if age_days < 0 or half_life_days <= 0:
        raise CredibilityPolicyError("invalid decay inputs")
    return exp(-0.6931471805599453 * age_days / half_life_days)


@dataclass(frozen=True)
class CredibilityMetrics:
    reach_percentile: float = 0.5
    engagement_percentile: float = 0.5
    successful_tasks: int = 0
    claimed_tasks: int = 0
    active_weeks: int = 0
    observed_weeks: int = 0
    quality_score: float = 0.5
    safety_score: float = 1.0
    sample_size: int = 0
    verified_external_evidence: bool = False


@dataclass(frozen=True)
class CredibilityResult:
    score: float
    tier: str
    confidence: float
    reliability: float
    consistency: float


def tier_for_score(score: float) -> str:
    score = float(score)
    if score < 0 or score > 100:
        raise CredibilityPolicyError("score must be between 0 and 100")
    if score < 30:
        return "PROVISIONAL"
    if score < 50:
        return "EMERGING"
    if score < 70:
        return "ESTABLISHED"
    if score < 85:
        return "PROVEN"
    return "PREMIER"


def calculate_credibility(metrics: CredibilityMetrics) -> CredibilityResult:
    """Calculate a slow-moving, explainable credibility profile.

    The prior score of 50 prevents a new user from being permanently treated as
    the lowest tier.  Evidence increases confidence gradually; safety remains a
    hard influence and is never replaced by audience size.
    """
    if metrics.sample_size < 0:
        raise CredibilityPolicyError("sample size cannot be negative")
    reliability = smoothed_rate(metrics.successful_tasks, metrics.claimed_tasks,
                                 prior_mean=0.75, prior_strength=5)
    consistency = (metrics.active_weeks / metrics.observed_weeks
                   if metrics.observed_weeks else 0.5)
    raw = 100 * (
        0.25 * _clamp(metrics.reach_percentile)
        + 0.20 * _clamp(metrics.engagement_percentile)
        + 0.20 * reliability
        + 0.15 * _clamp(consistency)
        + 0.10 * _clamp(metrics.quality_score)
        + 0.10 * _clamp(metrics.safety_score)
    )
    evidence_bonus = 0.15 if metrics.verified_external_evidence else 0.0
    confidence = _clamp((metrics.sample_size / 30.0) + evidence_bonus)
    score = 50.0 * (1.0 - confidence) + raw * confidence
    score = round(_clamp(score, 0, 100), 2)
    return CredibilityResult(
        score=score,
        tier=tier_for_score(score),
        confidence=round(confidence, 4),
        reliability=round(reliability, 4),
        consistency=round(_clamp(consistency), 4),
    )


def post_performance_score(*, reach_percentile: float,
                           engagement_percentile: float,
                           own_baseline_ratio: float,
                           consistency: float = 0.5,
                           quality: float = 1.0) -> float:
    """Score one eligible post from normalized and smoothed components."""
    baseline = _clamp(own_baseline_ratio / 2.0)
    return round(100 * (
        0.35 * _clamp(reach_percentile)
        + 0.25 * _clamp(engagement_percentile)
        + 0.20 * baseline
        + 0.10 * _clamp(consistency)
        + 0.10 * _clamp(quality)
    ), 2)


def distribution_radius(credibility_score: float, boost_level: int = 0) -> int:
    """Return the number of adjacent distribution tiers allowed by policy.

    Boost expands access only after credibility has established basic eligibility;
    it cannot make a restricted or unsafe account eligible.
    """
    score = float(credibility_score)
    if not 0 <= score <= 100 or boost_level < 0:
        raise CredibilityPolicyError("invalid distribution inputs")
    base = 0 if score < 30 else 1 if score < 70 else 2
    return min(3, base + min(1, boost_level // 2))
