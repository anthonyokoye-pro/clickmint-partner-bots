"""Offline tests for credibility and performance policies."""
from credibility import (
    CredibilityMetrics,
    calculate_credibility,
    cohort_percentile,
    decay_weight,
    distribution_radius,
    post_performance_score,
    smoothed_rate,
    tier_for_score,
)


def test_small_samples_are_smoothed():
    assert smoothed_rate(1, 1, prior_mean=0.75, prior_strength=5) < 1
    assert smoothed_rate(75, 100, prior_mean=0.75, prior_strength=5) > 0.74


def test_cohort_percentile_and_decay():
    assert cohort_percentile(20, [10, 20, 30]) == 0.5
    assert 0 < decay_weight(30, 30) < 1


def test_new_user_gets_provisional_evidence_based_profile():
    result = calculate_credibility(CredibilityMetrics(
        reach_percentile=0.95,
        engagement_percentile=0.90,
        successful_tasks=0,
        claimed_tasks=0,
        sample_size=6,
        verified_external_evidence=True,
    ))
    assert result.score > 50
    assert result.tier in {"EMERGING", "ESTABLISHED", "PROVEN"}
    assert result.confidence < 1


def test_consistent_user_outscores_weak_user_without_using_subscribers():
    strong = calculate_credibility(CredibilityMetrics(
        reach_percentile=0.90, engagement_percentile=0.85,
        successful_tasks=45, claimed_tasks=50,
        active_weeks=8, observed_weeks=8, quality_score=0.9,
        safety_score=1.0, sample_size=50,
    ))
    weak = calculate_credibility(CredibilityMetrics(
        reach_percentile=0.10, engagement_percentile=0.05,
        successful_tasks=2, claimed_tasks=20,
        active_weeks=1, observed_weeks=8, quality_score=0.3,
        safety_score=0.8, sample_size=20,
    ))
    assert strong.score > weak.score
    assert tier_for_score(strong.score) != "PROVISIONAL"


def test_post_score_uses_relative_baseline():
    high_relative = post_performance_score(
        reach_percentile=0.7, engagement_percentile=0.7,
        own_baseline_ratio=1.5, consistency=0.8,
    )
    low_relative = post_performance_score(
        reach_percentile=0.7, engagement_percentile=0.7,
        own_baseline_ratio=0.2, consistency=0.8,
    )
    assert high_relative > low_relative


def test_boost_only_expands_existing_access_radius():
    assert distribution_radius(20, 0) == 0
    assert distribution_radius(60, 0) == 1
    assert distribution_radius(60, 2) == 2
