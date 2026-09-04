"""Unit tests for governance.py — the NEW feature logic (post limit, submission
gate, review queue, partner contract, role registry). Runs offline, no bot/network."""
import os
import tempfile

from store import JsonStore
from governance import (
    base_cap_for_size, daily_post_cap, classify_submission, SubmissionGate,
    ReviewQueue, PartnerContractRegistry, RoleRegistry, POST_CATEGORIES,
    SCOPES,
)


def fresh_store():
    d = tempfile.mkdtemp()
    return JsonStore(os.path.join(d, "t.json"))


# --- 1) POST LIMIT (size + performance) --------------------------------------
def test_cap_scales_with_size():
    assert base_cap_for_size(400) == 1
    assert base_cap_for_size(1200) == 2
    assert base_cap_for_size(3000) == 3
    assert base_cap_for_size(9000) == 4


def test_cap_scales_with_performance_band():
    # size 3000 (base 3) -> A=3, B=2.25->2, C=1.5->1
    assert daily_post_cap(3000, "A") == 3
    assert daily_post_cap(3000, "B") == 2
    assert daily_post_cap(3000, "C") == 1


def test_cap_floor_is_one_for_active():
    # smallest channel, worst band -> still >= 1 (never zeroed out if active)
    assert daily_post_cap(400, "C") == 1


def test_cap_zero_for_nonactive():
    assert daily_post_cap(9000, "A", status="RESTRICTED") == 0
    assert daily_post_cap(9000, "A", status="REMOVED") == 0


def test_cap_unlimited_for_owner():
    assert daily_post_cap(0, "C", is_owner=True) == -1


def test_cap_examples_from_review():
    # 600-sub C-band -> 1; 3000-sub B-band -> 2; 6000-sub A-band -> 4
    assert daily_post_cap(600, "C") == 1
    assert daily_post_cap(3000, "B") == 2
    assert daily_post_cap(6000, "A") == 4


# --- 2) SUBMISSION GATE ------------------------------------------------------
def test_category_must_be_valid():
    g = SubmissionGate(fresh_store())
    ok, verdict, why = g.gate("Cooking", "some text", [])
    assert ok is False and verdict == "block"
    ok, verdict, why = g.gate("Airdrops", "nice airdrop guide", [])
    assert ok is True and verdict == "pass"


def test_category_must_match_target_receive_types():
    g = SubmissionGate(fresh_store())
    ok, verdict, why = g.gate("DeFi", "a defi post", ["Airdrops"])
    assert ok is False and "not a category" in why
    ok, verdict, why = g.gate("Airdrops", "an airdrop post", ["Airdrops"])
    assert ok is True and verdict == "pass"
    # 'General' receive accepts anything
    ok, verdict, why = g.gate("DeFi", "a defi post", ["General"])
    assert ok is True
    # empty receive list = accepts all
    ok, verdict, why = g.gate("DeFi", "a defi post", [])
    assert ok is True


def test_hard_block_patterns():
    assert classify_submission("send me your seed phrase and I'll double it") == "block"
    assert classify_submission("DM me for 100x guaranteed profit") == "block"
    assert classify_submission("pay activation fee to unlock your funds") == "block"
    assert classify_submission("promote your channel with our ad service") == "block"
    assert classify_submission("DM @news for a 1000x coin") == "block"


def test_soft_review_patterns_route_to_human():
    assert classify_submission("this airdrop looks profitable, limited time") == "review"
    assert classify_submission("earn passive income with this guide") == "review"


def test_clean_post_passes():
    assert classify_submission("Here is a verified airdrop guide and deadlines.") == "pass"


def test_gate_returns_review_for_borderline():
    g = SubmissionGate(fresh_store())
    ok, verdict, why = g.gate("Airdrops", "earn money limited time", ["Airdrops"])
    assert ok is True and verdict == "review"


def test_gate_blocks_hard_violation():
    g = SubmissionGate(fresh_store())
    ok, verdict, why = g.gate("Airdrops", "DM me your seed phrase", ["Airdrops"])
    assert ok is False and verdict == "block"


# --- 3) REVIEW QUEUE ---------------------------------------------------------
def test_review_queue_flow():
    q = ReviewQueue(fresh_store())
    it = q.submit("Airdrops", "@a", "earn money limited time", "chat:x")
    assert it["status"] == "pending"
    assert len(q.pending()) == 1
    q.decide(it["id"], approve=True)
    assert q.pending() == []
    assert q.get(it["id"])["status"] == "approved"


# --- 4) PARTNER CONTRACT -----------------------------------------------------
def test_partner_contract_open_renew_close():
    pc = PartnerContractRegistry(fresh_store())
    rec = pc.open("@A", "@B", {"type": "Airdrops", "vol": "2/week",
                               "sched": "Mon & Thu 12:00 UTC", "dur": 30})
    assert rec["status"] == "ACTIVE"
    assert len(pc.list("ACTIVE")) == 1

    # renew (owner notified in bot layer)
    r = pc.renew(rec["id"], note="working well")
    assert r["status"] == "RENEWED" and len(r["renewed"]) == 1

    # close is NOT final until owner confirms
    rc = pc.request_close(rec["id"], by="@A", reason="not benefiting")
    assert rc["status"] == "CLOSE_REQUESTED"
    assert pc.get(rec["id"])["closed"] is None

    # owner final -> closed
    fin = pc.finalize_close(rec["id"], by="owner", note="recorded")
    assert fin["status"] == "CLOSED"
    assert pc.between("@A", "@B")[0]["status"] == "CLOSED"


def test_partner_contract_between():
    pc = PartnerContractRegistry(fresh_store())
    pc.open("@X", "@Y", {})
    assert len(pc.between("@X", "@Y")) == 1
    assert len(pc.between("@X", "@Z")) == 0


# --- 5) ROLE REGISTRY --------------------------------------------------------
def test_owner_role():
    r = RoleRegistry(fresh_store(), owner_user_id=12345)
    assert r.is_owner(12345)
    assert r.role(12345) == "owner"
    assert r.has_access(12345, "reward")
    assert r.has_access(12345, "partnership")


def test_admin_invite_code_not_self_serve():
    r = RoleRegistry(fresh_store(), owner_user_id=1)
    # a random user cannot be admin without a code
    assert r.is_admin(99, "reward") is False
    code = r.create_invite(["reward"], created_by=1)
    ok, msg = r.redeem_invite(99, code)
    assert ok is True
    assert r.is_admin(99, "reward") is True
    assert r.is_admin(99, "partnership") is False   # scoped!
    assert r.has_access(99, "reward") is True


def test_invite_code_used_once():
    r = RoleRegistry(fresh_store(), owner_user_id=1)
    code = r.create_invite(["reward"], created_by=1)
    assert r.redeem_invite(99, code)[0] is True
    assert r.redeem_invite(100, code)[0] is False   # already used


def test_invalid_code_rejected():
    r = RoleRegistry(fresh_store(), owner_user_id=1)
    ok, msg = r.redeem_invite(99, "NOPE123")
    assert ok is False


def test_revoke_admin():
    r = RoleRegistry(fresh_store(), owner_user_id=1)
    code = r.create_invite(["reward", "partnership"], created_by=1)
    r.redeem_invite(99, code)
    assert r.is_admin(99, "reward") is True
    r.revoke_admin(99)
    assert r.is_admin(99, "reward") is False


# --- owner exemption via NUMERIC id (end-to-end) ---
def test_owner_credit_exempt_by_user_id():
    from core import CreditLedger
    st = fresh_store()
    led = CreditLedger(st)
    led.owner_user_id = 555                       # the numeric OWNER_USER_ID
    # a member registers under a username, and their id is remembered:
    m = led.register("@SomeChan", 400)
    led.set_user_id("@SomeChan", 555)
    # credit checks pass + spend charges nothing:
    ok, why = led.can_spend("@SomeChan")
    assert ok is True and why == "owner is exempt"
    ok, why = led.spend("@SomeChan", 5)           # would cost 5 credits if not exempt
    assert ok is True and "exempt" in why
    assert led.balance("@SomeChan")["balance"] == 2   # onboarding seed untouched -> not charged


def test_non_owner_not_exempt_by_user_id():
    from core import CreditLedger
    st = fresh_store()
    led = CreditLedger(st)
    led.owner_user_id = 555
    led.register("@NormalChan", 400)
    led.set_user_id("@NormalChan", 999)
    ok, why = led.can_spend("@NormalChan")
    assert ok is False                                # not the owner -> must earn first




# run all (keep at the very END so every test_ function above is included)
import traceback
if __name__ == "__main__":
    all_fns = [v for k, v in sorted(globals().items())
               if k.startswith("test_") and callable(v)]
    passed = 0
    failed = []
    for fn in all_fns:
        try:
            fn()
            passed += 1
            print(f"  PASS {fn.__name__}")
        except Exception as e:
            failed.append(fn.__name__)
            print(f"  FAIL {fn.__name__}: {e}")
            traceback.print_exc()
    print(f"\n{passed}/{len(all_fns)} governance tests passed")
    if failed:
        print("FAILED:", failed)
