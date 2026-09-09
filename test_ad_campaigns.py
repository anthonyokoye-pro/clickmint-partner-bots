"""Consent-aware advertising: every compliance precondition is a hard stop."""
import tempfile
from pathlib import Path

from ad_campaigns import AdCampaignStore, AdPolicyError


def _store(enabled=True):
    d = tempfile.TemporaryDirectory(prefix="clickmint-ads-")
    return d, AdCampaignStore(Path(d.name) / "ads.sqlite3", enabled=enabled)


def _expect(fn, needle):
    try:
        fn()
    except AdPolicyError as exc:
        assert needle in str(exc), str(exc)
    else:
        raise AssertionError(f"expected AdPolicyError containing {needle!r}")


def test_no_terms_no_campaigns_no_consent():
    d, ads = _store()
    with d:
        _expect(lambda: ads.create_campaign(title="t", advertiser_label="a", category="Guides", text="x", created_by=1), "publish advertising terms")
        _expect(lambda: ads.grant_consent("@c", owner_id=1, terms_version=1), "no advertising terms")


def test_full_lifecycle_requires_consent_review_and_flag():
    d, ads = _store(enabled=True)
    with d:
        terms = ads.publish_terms("v1 terms", published_by="owner")
        assert terms["version"] == 1
        cid = ads.create_campaign(title="Launch", advertiser_label="Acme", category="Guides", text="hello", created_by=9)
        # cannot queue a draft
        _expect(lambda: ads.queue(cid, actor_id=9), "only an approved campaign")
        assert ads.submit_for_review(cid, actor_id=9)
        _expect(lambda: ads.review(cid, approved=True, reviewer_id="owner", reason=""), "reason is required")
        ads.review(cid, approved=True, reviewer_id="owner", reason="clean copy, disclosed advertiser")
        # approved, but nobody consented
        _expect(lambda: ads.queue(cid, actor_id=9), "no destination has consented")
        ads.grant_consent("@yes", owner_id=100, terms_version=1, categories=["Guides"])
        ads.grant_consent("@other", owner_id=101, terms_version=1, categories=["DeFi"])   # wrong category
        ads.grant_consent("@any", owner_id=102, terms_version=1)                          # all categories
        assert ads.queue(cid, actor_id=9) == 2
        claimed = ads.claim(limit=10)
        assert {c["destination_id"] for c in claimed} == {"@yes", "@any"}
        for c in claimed:
            ads.complete(c["delivery_id"], 555)
        assert ads.get_campaign(cid)["status"] == "completed"
        assert ads.summary(cid)["deliveries"] == {"sent": 2}


def test_kill_switch_blocks_queue_and_claim():
    d, ads = _store(enabled=False)
    with d:
        ads.publish_terms("v1", published_by="owner")
        ads.grant_consent("@yes", owner_id=1, terms_version=1)
        cid = ads.create_campaign(title="t", advertiser_label="a", category="Guides", text="x", created_by=1)
        ads.submit_for_review(cid, actor_id=1)
        ads.review(cid, approved=True, reviewer_id="owner", reason="ok")
        _expect(lambda: ads.queue(cid, actor_id=1), "disabled")
        ads.enabled = True
        assert ads.queue(cid, actor_id=1) == 1
        ads.enabled = False
        assert ads.claim() == [], "a disabled worker must claim nothing"


def test_new_terms_invalidate_old_consent_and_stale_approvals():
    d, ads = _store()
    with d:
        ads.publish_terms("v1", published_by="owner")
        ads.grant_consent("@c", owner_id=1, terms_version=1)
        cid = ads.create_campaign(title="t", advertiser_label="a", category="Guides", text="x", created_by=1)
        ads.submit_for_review(cid, actor_id=1)
        ads.review(cid, approved=True, reviewer_id="owner", reason="ok")
        ads.publish_terms("v2 — stricter", published_by="owner")
        assert ads.consent("@c") is None, "old consent must not carry over to new terms"
        _expect(lambda: ads.grant_consent("@c", owner_id=1, terms_version=1), "current terms (v2)")
        _expect(lambda: ads.queue(cid, actor_id=1), "terms changed since approval")
        # re-consent and redraft under v2
        ads.grant_consent("@c", owner_id=1, terms_version=2)
        cid2 = ads.create_campaign(title="t2", advertiser_label="a", category="Guides", text="x", created_by=1)
        ads.submit_for_review(cid2, actor_id=1)
        ads.review(cid2, approved=True, reviewer_id="owner", reason="ok")
        assert ads.queue(cid2, actor_id=1) == 1


def test_revocation_cancels_pending_and_claim_rechecks_consent():
    d, ads = _store()
    with d:
        ads.publish_terms("v1", published_by="owner")
        ads.grant_consent("@a", owner_id=1, terms_version=1)
        ads.grant_consent("@b", owner_id=2, terms_version=1)
        cid = ads.create_campaign(title="t", advertiser_label="a", category="Guides", text="x", created_by=1)
        ads.submit_for_review(cid, actor_id=1)
        ads.review(cid, approved=True, reviewer_id="owner", reason="ok")
        assert ads.queue(cid, actor_id=1) == 2
        assert ads.revoke_consent("@a", owner_id=1, reason="changed my mind")
        claimed = ads.claim()
        assert [c["destination_id"] for c in claimed] == ["@b"]
        assert ads.summary(cid)["deliveries"].get("cancelled") == 1
        # revoke between claim and send is also respected on the next claim
        ads.release(claimed[0]["delivery_id"], retry_seconds=0)
        ads.revoke_consent("@b", owner_id=2)
        assert ads.claim() == []


def test_edit_resets_review_and_rejected_can_be_fixed():
    d, ads = _store()
    with d:
        ads.publish_terms("v1", published_by="owner")
        cid = ads.create_campaign(title="t", advertiser_label="a", category="Guides", text="x", created_by=1)
        ads.submit_for_review(cid, actor_id=1)
        ads.review(cid, approved=False, reviewer_id="owner", reason="undisclosed affiliate link")
        assert ads.get_campaign(cid)["status"] == "rejected"
        assert ads.update_draft(cid, text="fixed copy")
        c = ads.get_campaign(cid)
        assert c["status"] == "draft" and c["reviewed_by"] is None
        ads.submit_for_review(cid, actor_id=1)
        ads.review(cid, approved=True, reviewer_id="owner", reason="disclosure added")
        assert not ads.update_draft(cid, text="sneaky edit after approval")
        actions = [e["action"] for e in ads.events(cid)]
        assert actions[0] == "APPROVED" and "REJECTED" in actions and "EDITED" in actions


if __name__ == "__main__":
    for test in (test_no_terms_no_campaigns_no_consent,
                 test_full_lifecycle_requires_consent_review_and_flag,
                 test_kill_switch_blocks_queue_and_claim,
                 test_new_terms_invalidate_old_consent_and_stale_approvals,
                 test_revocation_cancels_pending_and_claim_rechecks_consent,
                 test_edit_resets_review_and_rejected_can_be_fixed):
        test()
        print(f"PASS {test.__name__}")
    print("\nALL AD CAMPAIGN TESTS PASSED (6)")
