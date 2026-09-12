"""Migrate legacy JSON Mint aggregates into the transactional ledger.

Usage:
    MINT_LEDGER_MODE=legacy python3 migrate_mint.py

The migration is additive and does not delete or rewrite the JSON store. Run it
against a backup copy first, then compare balances before activating
MINT_LEDGER_MODE=transactional in staging.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import config
from mint_ledger import InsufficientMint, TransactionalMintLedger


def migrate(store_path: str, db_path: str, *, dry_run: bool = False) -> dict:
    path = Path(store_path)
    payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    members = payload.get("ledger", {})
    if not isinstance(members, dict):
        raise ValueError("legacy store has no valid ledger object")
    if dry_run:
        db = None
    else:
        db = TransactionalMintLedger(db_path)
    report = {"members": 0, "credits": 0, "debits": 0, "skipped": 0, "errors": []}
    for username, member in members.items():
        if not isinstance(member, dict):
            report["skipped"] += 1
            continue
        uid = str(member.get("user_id") or username)
        earned = max(0, int(member.get("earned", 0) or 0))
        spent = max(0, int(member.get("spent", 0) or 0))
        current = max(0, int(member.get("balance", 0) or 0))
        seed = max(0, current - earned + spent)
        report["members"] += 1
        report["credits"] += seed + earned
        report["debits"] += spent
        if dry_run:
            continue
        try:
            db.ensure_account(uid)
            if seed:
                db.credit(uid, seed, entry_type="MIGRATION_SEED", idempotency_key=f"migration:seed:{uid}")
            if earned:
                db.credit(uid, earned, entry_type="MIGRATION_EARNED", idempotency_key=f"migration:earned:{uid}")
            if spent:
                db.debit(uid, spent, entry_type="MIGRATION_SPENT", idempotency_key=f"migration:spent:{uid}")
            actual = db.balance(uid)
            if actual != current:
                raise ValueError(f"balance mismatch: expected {current}, got {actual}")
        except (InsufficientMint, ValueError) as exc:
            report["errors"].append({"user": username, "error": str(exc)})
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", default=config.REWARD_STORE_PATH)
    parser.add_argument("--db", default=config.MINT_DB_PATH)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    report = migrate(args.store, args.db, dry_run=args.dry_run)
    print(json.dumps(report, indent=2))
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
