"""Delivery audit + roles on shared SQLite (decision 2026-09-09 #2)."""
import os
import tempfile

from store import JsonStore
from governance import RoleRegistry
from platform_store import DeliveryAudit, SharedKV


def _paths():
    d = tempfile.mkdtemp()
    return os.path.join(d, "platform.sqlite3"), os.path.join(d, "reward.json"), os.path.join(d, "partner.json")


def test_delivery_log_migrates_once_and_keeps_json_copy():
    db, rj, _ = _paths()
    legacy = JsonStore(rj)
    legacy["delivery_log"] = [{"bot": "reward", "sender": "@a", "target_channel": "@b", "status": "offered",
                               "forward_valid": True, "ts": "2026-01-01 00:00:00"}]
    legacy.sync()
    audit = DeliveryAudit(db, legacy=legacy)
    assert audit.count() == 1 and legacy.get("delivery_log") == [] and len(legacy.get("delivery_log_migrated")) == 1
    DeliveryAudit(db, legacy=legacy)                       # reopen: no double import
    assert audit.count() == 1
    print("PASS delivery log migrates once, JSON copy kept as *_migrated")


def test_audit_update_is_targeted_and_queries_are_indexed():
    db, _, _ = _paths()
    audit = DeliveryAudit(db)
    for i in range(5):
        audit.record(bot="reward", sender="@a", target_channel="@b", mode="chain", status="offered", forward_valid=True, n=i)
    audit.record(bot="reward", sender="@a", target_channel="@c", mode="chain", status="offered", forward_valid=False)
    latest = audit.latest_for(sender="@a", target_channel="@b", status="offered")
    assert latest["n"] == 4, latest
    audit.update(latest["id"], status="delivered")
    assert audit.latest_for(sender="@a", target_channel="@b", status="offered")["n"] == 3
    assert audit.all()[-2]["status"] == "delivered" and audit.last(1)[0]["target_channel"] == "@c"
    assert len(audit.invalid_records()) == 1 and "1 delivery" in audit.summary() and "delivered: 1" in audit.summary()
    print("PASS audit rows are updated in place and looked up by index")


def test_roles_are_shared_across_bots_and_merged_on_migration():
    db, rj, pj = _paths()
    r_json, p_json = JsonStore(rj), JsonStore(pj)
    r_json["roles_users"] = {"5": {"role": "admin", "scope": ["reward"], "active": True}}; r_json.sync()
    p_json["roles_users"] = {"6": {"role": "admin", "scope": ["partnership"], "active": True}}; p_json.sync()
    reward_roles = RoleRegistry(SharedKV(db, legacy=r_json), owner_user_id=1)
    partner_roles = RoleRegistry(SharedKV(db, legacy=p_json), owner_user_id=1)
    # Both legacy files merged into ONE table; each bot sees the other's admins.
    assert reward_roles.is_admin(6, "partnership") and partner_roles.is_admin(5, "reward")
    assert r_json.get("roles_users") == {} and p_json.get("roles_users_migrated")
    # A code created by the admin bot (third connection) is redeemable in either bot.
    code = RoleRegistry(SharedKV(db), owner_user_id=1).create_invite(["reward", "partnership"], created_by=1)
    ok, _ = partner_roles.redeem_invite(9, code)
    assert ok and reward_roles.is_admin(9, "reward") and reward_roles.is_admin(9, "partnership")
    ok2, _ = reward_roles.redeem_invite(10, code)
    assert not ok2, "single-use code redeemed twice"
    assert reward_roles.revoke_admin(9) and not partner_roles.is_admin(9)
    print("PASS roles are one shared table: cross-bot visibility, single code, single-use")


TESTS = [test_delivery_log_migrates_once_and_keeps_json_copy, test_audit_update_is_targeted_and_queries_are_indexed,
         test_roles_are_shared_across_bots_and_merged_on_migration]

if __name__ == "__main__":
    for fn in TESTS:
        fn()
    print(f"\nALL PLATFORM STORE TESTS PASSED ({len(TESTS)})")
