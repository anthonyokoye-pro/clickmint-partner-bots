import tempfile
from pathlib import Path

from enforcement import EnforcementError, EnforcementStore
from eligibility import EligibilityContext, can_claim_task


def test_lifecycle_and_audit_timeline():
    with tempfile.TemporaryDirectory() as directory:
        store = EnforcementStore(Path(directory) / "enforcement.sqlite3")
        assert store.get("u1", "user")["state"] == "ACTIVE"
        report = store.report("reporter", "u1", "user", "suspected scam")
        evidence = store.add_evidence("u1", "user", "message", "telegram:123/456", captured_by="admin", report_id=report["report_id"])
        store.review_report(report["report_id"], "UNDER_REVIEW", reviewer_id="admin")
        state = store.enforce("u1", "user", "RESTRICTED", actor_id="admin", reason="reviewed risk", related_report_id=report["report_id"], evidence_ids=[evidence["evidence_id"]])
        assert state["state"] == "RESTRICTED"
        assert not store.allowed("u1", "user", "distribution")
        store.restore("u1", "user", actor_id="admin", reason="review cleared")
        assert store.allowed("u1", "user", "distribution")
        assert len(store.timeline("u1", "user")) == 2


def test_ban_and_safe_mode_fail_closed():
    with tempfile.TemporaryDirectory() as directory:
        store = EnforcementStore(Path(directory) / "enforcement.sqlite3")
        store.enforce("c1", "channel", "BANNED", actor_id="admin", reason="confirmed abuse")
        assert not store.allowed("c1", "channel", "tasks")
        store.set_emergency(True, actor_id="owner", reason="incident response")
        assert not store.allowed("unknown", "user", "registration")
        store.set_emergency(False, actor_id="owner", reason="incident resolved")
        assert store.allowed("unknown", "user", "registration")


def test_reports_require_reason_and_review_is_explicit():
    with tempfile.TemporaryDirectory() as directory:
        store = EnforcementStore(Path(directory) / "enforcement.sqlite3")
        try:
            store.report("r", "u", "user", "")
        except EnforcementError:
            pass
        else:
            raise AssertionError("empty report reason accepted")
        report = store.report("r", "u", "user", "false report")
        assert store.review_report(report["report_id"], "DISMISSED", reviewer_id="admin")["status"] == "DISMISSED"


def test_eligibility_blocks_enforcement_and_safe_mode():
    result = can_claim_task(EligibilityContext(
        account_active=True, destination_verified=True, destination_status="ACTIVE",
        credibility_score=80, category_match=True, enforcement_state="SUSPENDED",
    ))
    assert not result.eligible
    assert any("suspended" in reason for reason in result.reasons)


def test_appeal_workflow_is_human_and_overturn_restores():
    with tempfile.TemporaryDirectory() as directory:
        store = EnforcementStore(Path(directory) / "enforcement.sqlite3")
        try:
            store.appeal("u1", "user", submitted_by="u1", statement="nothing happened")
        except EnforcementError:
            pass
        else:
            raise AssertionError("appeal accepted for an ACTIVE entity")
        store.enforce("u1", "user", "SUSPENDED", actor_id="admin", reason="reviewed")
        appeal = store.appeal("u1", "user", submitted_by="u1", statement="please review")
        assert appeal["status"] == "OPEN"
        try:
            store.appeal("u1", "user", submitted_by="u1", statement="again")
        except EnforcementError:
            pass
        else:
            raise AssertionError("duplicate open appeal accepted")
        # filing changes nothing
        assert store.get("u1", "user")["state"] == "SUSPENDED"
        upheld = store.decide_appeal(appeal["appeal_id"], "UPHELD", decided_by="admin", notes="evidence stands")
        assert upheld["status"] == "UPHELD" and store.get("u1", "user")["state"] == "SUSPENDED"
        second = store.appeal("u1", "user", submitted_by="u1", statement="new evidence")
        store.decide_appeal(second["appeal_id"], "OVERTURNED", decided_by="owner", notes="mistaken identity")
        assert store.get("u1", "user")["state"] == "ACTIVE"
        assert store.timeline("u1", "user")[0]["action"] == "RESTORE"
        status = store.safety_status()
        assert status["open_appeals"] == 0 and status["entities_by_state"].get("ACTIVE") == 1


def test_reports_and_evidence_are_listable():
    with tempfile.TemporaryDirectory() as directory:
        store = EnforcementStore(Path(directory) / "enforcement.sqlite3")
        r1 = store.report("a", "c1", "channel", "spam")
        store.report("b", "c2", "channel", "scam")
        store.add_evidence("c1", "channel", "link", "https://t.me/c1/5", captured_by="admin", report_id=r1["report_id"])
        assert len(store.list_reports("PENDING")) == 2
        assert [r["entity_id"] for r in store.list_reports(entity_id="c1")] == ["c1"]
        assert len(store.evidence_for("c1", "channel", report_id=r1["report_id"])) == 1
        assert store.safety_status()["open_reports"] == 2


def test_gate_fails_closed_and_exempts_owner_from_entity_state():
    from enforcement_gate import EnforcementGate
    with tempfile.TemporaryDirectory() as directory:
        store = EnforcementStore(Path(directory) / "enforcement.sqlite3")
        gate = EnforcementGate(store, owner_user_id=7)
        assert gate.check(1, "user", "distribution")
        store.enforce(1, "user", "RESTRICTED", actor_id="admin", reason="reviewed")
        decision = gate.check(1, "user", "distribution")
        assert not decision and decision.state == "RESTRICTED"
        assert "appeal" in gate.block_message(decision)
        # flagged = under review, still participates
        store.enforce(2, "user", "FLAGGED", actor_id="admin", reason="watch")
        assert gate.check(2, "user", "distribution")
        # owner is exempt from entity state...
        store.enforce(7, "user", "BANNED", actor_id="admin", reason="cannot happen but test it")
        assert gate.check(7, "user", "distribution")
        # ...but not from safe mode
        store.set_emergency(True, actor_id="owner", reason="incident")
        assert not gate.check(7, "user", "automated_posting").allowed
        assert gate.check(7, "user", "automated_posting").safe_mode
        # check_many returns the first blocking subject
        store.set_emergency(False, actor_id="owner", reason="over")
        many = gate.check_many([(3, "user"), (1, "user")], "tasks")
        assert not many and many.state == "RESTRICTED"
        # no store => permissive (legacy tests); broken store => closed
        assert EnforcementGate(None).check(1, "user", "tasks")

        class Broken:
            def emergency_enabled(self, *_): raise RuntimeError("db gone")
        assert not EnforcementGate(Broken()).check(1, "user", "tasks")


if __name__ == "__main__":
    for test_case in (test_lifecycle_and_audit_timeline, test_ban_and_safe_mode_fail_closed,
                      test_reports_require_reason_and_review_is_explicit,
                      test_eligibility_blocks_enforcement_and_safe_mode,
                      test_appeal_workflow_is_human_and_overturn_restores,
                      test_reports_and_evidence_are_listable,
                      test_gate_fails_closed_and_exempts_owner_from_entity_state):
        test_case()
        print(f"PASS {test_case.__name__}")
