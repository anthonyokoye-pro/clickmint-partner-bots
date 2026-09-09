"""SQLite verification store: shared credential ownership + JSON migration."""
from __future__ import annotations

import os
import tempfile
import time

os.environ.setdefault("CLICKMINT_CREDENTIAL_KEY", "test-only-credential-key-please-change")

from store import JsonStore
from verification_store import VerificationStore, CRED_KEY, DEST_KEY
from channel_registry import ChannelRegistry
from telegram_verification import BotCredentialStore, CredentialError
from destination_state import DestinationStateMachine, VState


def _tmp(name):
    return os.path.join(tempfile.mkdtemp(prefix="cm-vs-"), name)


def test_migrates_legacy_json_once_and_keeps_a_copy():
    legacy_path = _tmp("reward_ledger.json")
    legacy = JsonStore(legacy_path)
    legacy[DEST_KEY] = {"@alice": {"chat_id": "@alice", "owner_id": 1, "verified_state": "VERIFIED",
                                   "status": "ACTIVE", "last_verified_at": 100}}
    legacy[CRED_KEY] = {"1": {"owner_id": 1, "bot_id": 9001, "username": "alicebot", "nonce": "x", "ciphertext": "y"}}
    legacy.sync()
    db = _tmp("verification.sqlite3")
    vs = VerificationStore(db, legacy=legacy)
    assert vs.get(DEST_KEY)["@alice"]["status"] == "ACTIVE"
    assert vs.get(CRED_KEY)["1"]["bot_id"] == 9001
    assert legacy.get(DEST_KEY) == {} and legacy.get(f"{DEST_KEY}_migrated")["@alice"]["owner_id"] == 1
    # Re-opening does not re-import (JSON is now empty) and does not lose rows.
    vs2 = VerificationStore(db, legacy=JsonStore(legacy_path))
    assert "@alice" in vs2.get(DEST_KEY)
    assert vs2._conn.execute("SELECT v FROM meta WHERE k='migrated:managed_channels'").fetchone()
    print("PASS legacy JSON rows are migrated once; a _migrated copy is kept")


def test_registry_and_state_machine_work_on_sqlite():
    vs = VerificationStore(_tmp("v.sqlite3"), legacy=JsonStore(_tmp("l.json")))
    reg = ChannelRegistry(vs)
    sm = DestinationStateMachine(reg)
    reg.add(7, "@seven", "@seven", "channel", ["General"], size=10)
    sm.transition(7, "@seven", VState.VERIFYING, reason="t", source="owner")
    sm.transition(7, "@seven", VState.DEGRADED, reason="t", source="scan")
    # Fresh process view: read straight from SQLite.
    other = VerificationStore(vs.path, legacy=JsonStore(_tmp("l2.json")))
    row = ChannelRegistry(other).get("@seven")
    assert row["verified_state"] == "DEGRADED" and row["status"] == "DEGRADED"
    assert len(row["state_history"]) == 2
    # Indexed due-query: DEGRADED rechecks every 30 min.
    assert other.destinations_due(now=int(time.time()) + 29 * 60) == []
    due = other.destinations_due(now=int(time.time()) + 31 * 60)
    assert [r["chat_id"] for r in due] == ["@seven"]
    print("PASS registry + state machine persist to SQLite; due-query uses the index")


def test_credentials_are_shared_across_both_bot_processes():
    """The step-9 requirement: one bot per ClickMint account, across Reward AND Partnership."""
    db = _tmp("shared.sqlite3")
    reward = BotCredentialStore(VerificationStore(db, legacy=JsonStore(_tmp("r.json"))))
    partner = BotCredentialStore(VerificationStore(db, legacy=JsonStore(_tmp("p.json"))))
    reward.save(1001, "9001:tok", {"id": 9001, "username": "alicebot"})
    assert partner.public(1001)["bot_id"] == 9001, "partnership must see the reward connection"
    assert partner.token(1001) == "9001:tok"
    try:
        partner.save(2002, "9001:tok", {"id": 9001, "username": "alicebot"})
        raise AssertionError("second owner claimed the same bot via the other store")
    except CredentialError as exc:
        assert "another ClickMint account" in str(exc)
    assert partner.store.owner_of_bot(9001) == 1001
    reward.remove(1001)
    # No explicit reload: the other connection must notice the commit by itself.
    assert partner.public(1001) is None, "cross-process cache must self-invalidate"
    reward.save(1001, "9001:tok", {"id": 9001, "username": "alicebot"})
    assert partner.public(1001)["bot_id"] == 9001
    print("PASS the same bot cannot be owned by two accounts across the two bots")


def test_other_keys_still_go_to_legacy_json():
    legacy = JsonStore(_tmp("l.json"))
    vs = VerificationStore(_tmp("v.sqlite3"), legacy=legacy)
    vs["ledger"] = {"@x": 1}; vs.sync()
    assert legacy.get("ledger") == {"@x": 1}
    assert "ledger" in vs and DEST_KEY in vs
    print("PASS non-verification keys are delegated to the legacy JsonStore untouched")


TESTS = [test_migrates_legacy_json_once_and_keeps_a_copy, test_registry_and_state_machine_work_on_sqlite,
         test_credentials_are_shared_across_both_bot_processes, test_other_keys_still_go_to_legacy_json]

if __name__ == "__main__":
    for fn in TESTS:
        fn()
    print(f"\nALL VERIFICATION STORE TESTS PASSED ({len(TESTS)})")
