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


if __name__ == "__main__":
    for test_case in (test_lifecycle_and_audit_timeline, test_ban_and_safe_mode_fail_closed,
                      test_reports_require_reason_and_review_is_explicit,
                      test_eligibility_blocks_enforcement_and_safe_mode):
        test_case()
        print(f"PASS {test_case.__name__}")
