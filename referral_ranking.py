"""Separate, quality-based referral ranking policy."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReferralRecord:
    referrer_id: str
    referred_id: str
    status: str
    retained: bool = False
    qualified_activity_count: int = 0
    fraud_flag: bool = False


def qualified(record: ReferralRecord, *, minimum_activities: int = 1,
              require_retention: bool = True) -> bool:
    if record.fraud_flag or record.status in {"rejected", "reversed"}:
        return False
    if record.status not in {"qualified", "pending"}:
        return False
    if record.qualified_activity_count < minimum_activities:
        return False
    return not require_retention or record.retained


def rank_referrers(records: list[ReferralRecord], *, minimum_activities: int = 1,
                   require_retention: bool = True) -> list[dict]:
    grouped: dict[str, list[ReferralRecord]] = {}
    for record in records:
        grouped.setdefault(record.referrer_id, []).append(record)
    rows = []
    for referrer_id, items in grouped.items():
        valid = [r for r in items if qualified(
            r, minimum_activities=minimum_activities,
            require_retention=require_retention)]
        retained = sum(r.retained for r in valid)
        activity = sum(r.qualified_activity_count for r in valid)
        rows.append({
            "referrer_id": referrer_id,
            "qualified_referrals": len(valid),
            "retained_referrals": retained,
            "activity_total": activity,
            "score": len(valid) * 60 + retained * 25 + min(activity, 100) * 0.15,
        })
    return sorted(rows, key=lambda r: (
        -r["score"], -r["retained_referrals"], -r["qualified_referrals"], r["referrer_id"]
    ))


def allocate_monthly_rewards(ranking: list[dict], *, pool: int,
                             winners: int) -> list[dict]:
    if pool < 0 or winners < 0:
        raise ValueError("pool and winners must be non-negative")
    selected = ranking[:winners]
    if not selected or pool == 0:
        return []
    # Weighted by rank, with integer remainder distributed from the top.
    weights = list(range(len(selected), 0, -1))
    total = sum(weights)
    amounts = [pool * weight // total for weight in weights]
    for index in range(pool - sum(amounts)):
        amounts[index % len(amounts)] += 1
    return [{**row, "reward_amount": amount}
            for row, amount in zip(selected, amounts)]
