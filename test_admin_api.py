import hashlib
import hmac
import json
import tempfile
import time
from pathlib import Path
from urllib.parse import urlencode

from admin_api import AdminReadAPI
from channel_registry import ChannelRegistry
from performance_snapshots import PerformanceSnapshotRepository
from task_marketplace import TaskMarketplace
from broadcast_queue import BroadcastQueue
from store import JsonStore


class Roles:
    def is_admin(self, uid):
        return int(uid) == 9


def signed(token, values):
    check = "\n".join(f"{key}={value}" for key, value in sorted(values.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(values) + "&hash=" + digest


def test_authenticated_dashboard_is_read_only():
    directory = tempfile.TemporaryDirectory(prefix="clickmint-api-")
    try:
        root = Path(directory.name)
        store = JsonStore(str(root / "store.json"))
        channels = ChannelRegistry(store)
        channels.add(9, "-1001", "@demo", "channel", ["Guides"], bot_added=True)
        market = TaskMarketplace(root / "tasks.sqlite3")
        snapshots = PerformanceSnapshotRepository(root / "credibility.sqlite3")
        broadcasts = BroadcastQueue(root / "broadcast.sqlite3")
        api = AdminReadAPI(bot_token="token", owner_id=8, roles=Roles(),
                           channels=channels, marketplace=market,
                           snapshots=snapshots, broadcasts=broadcasts)
        init_data = signed("token", {
            "auth_date": str(int(time.time())), "user": json.dumps({"id": 9})
        })
        result = api.dashboard(init_data)
        assert result["user"]["id"] == 9
        assert result["channels"][0]["status"] == "ACTIVE"
    finally:
        directory.cleanup()


def test_reports_appeals_and_owner_only_enforcement():
    from admin_api import AdminAuthorizationError
    from enforcement import EnforcementStore
    directory = tempfile.TemporaryDirectory(prefix="clickmint-api-")
    try:
        root = Path(directory.name)
        store = JsonStore(str(root / "store.json"))
        enforcement = EnforcementStore(root / "enforcement.sqlite3")
        api = AdminReadAPI(bot_token="token", owner_id=8, roles=Roles(),
                           channels=ChannelRegistry(store), marketplace=TaskMarketplace(root / "t.sqlite3"),
                           snapshots=PerformanceSnapshotRepository(root / "c.sqlite3"),
                           broadcasts=BroadcastQueue(root / "b.sqlite3"), enforcement=enforcement)
        now = str(int(time.time()))
        admin = signed("token", {"auth_date": now, "user": json.dumps({"id": 9})})
        owner = signed("token", {"auth_date": now, "user": json.dumps({"id": 8})})
        report = api.file_report(admin, "@spammy", "channel", "repeated scam links")
        assert api.safety_overview(admin)["status"]["open_reports"] == 1
        evidence = api.add_evidence(admin, "@spammy", "channel", "link", "https://t.me/spammy/1", report["report_id"])
        details = api.report_details(admin, report["report_id"])
        assert details["evidence"][0]["evidence_id"] == evidence["evidence_id"]
        # a review decision never changes state
        api.review_report(admin, report["report_id"], "UNDER_REVIEW", "looking")
        assert enforcement.get("@spammy", "channel")["state"] == "ACTIVE"
        # scoped admins cannot enforce
        try:
            api.enforce_entity(admin, "@spammy", "channel", "restricted", "confirmed", related_report_id=report["report_id"])
        except AdminAuthorizationError:
            pass
        else:
            raise AssertionError("admin enforced without owner approval")
        # owner enforcement linked to the report closes the report with that outcome
        result = api.enforce_entity(owner, "@spammy", "channel", "restricted", "confirmed", 3600, related_report_id=report["report_id"])
        assert result["state"] == "RESTRICTED"
        assert enforcement.report_by_id(report["report_id"])["status"] == "RESTRICTED"
        for bad in ({"duration_seconds": 0}, {"duration_seconds": 10 ** 9}, {"action": "nuke"}):
            try:
                api.enforce_entity(owner, "@x", "channel", bad.get("action", "restricted"), "r", bad.get("duration_seconds"))
            except ValueError:
                pass
            else:
                raise AssertionError(f"accepted {bad}")
        # appeals: admins may review/uphold, only the owner may overturn
        appeal = enforcement.appeal("@spammy", "channel", submitted_by="u", statement="not me")
        api.decide_appeal(admin, appeal["appeal_id"], "UNDER_REVIEW")
        try:
            api.decide_appeal(admin, appeal["appeal_id"], "OVERTURNED")
        except AdminAuthorizationError:
            pass
        else:
            raise AssertionError("admin overturned enforcement")
        assert api.decide_appeal(owner, appeal["appeal_id"], "OVERTURNED", "verified")["status"] == "OVERTURNED"
        assert enforcement.get("@spammy", "channel")["state"] == "ACTIVE"
        # the appellant is told the final decision via the durable outbox (numeric ids only)
        from mint_ledger import TransactionalMintLedger
        class Facade:
            def __init__(self): self.tx = TransactionalMintLedger(root / "mint.sqlite3"); self.ledger = {}
        api.ledger = Facade()
        enforcement.enforce("@spammy", "channel", "RESTRICTED", actor_id="owner", reason="again")
        second = enforcement.appeal("@spammy", "channel", submitted_by=4242, statement="second")
        api.decide_appeal(admin, second["appeal_id"], "UNDER_REVIEW")
        assert api.ledger.tx.claim_outbox(limit=5) == [], "interim states must not notify"
        api.decide_appeal(admin, second["appeal_id"], "UPHELD", "evidence stands")
        events = api.ledger.tx.claim_outbox(limit=5)
        assert len(events) == 1 and events[0]["recipient_id"] == "4242" and "stands" in events[0]["payload"]
        assert {a["status"] for a in api.list_appeals(admin)} == {"OVERTURNED", "UPHELD"}
    finally:
        directory.cleanup()


def test_ads_are_scoped_consent_gated_and_owner_reviewed():
    from admin_api import AdminAuthorizationError
    from ad_campaigns import AdCampaignStore, AdPolicyError

    class ScopedRoles:
        # 9 = network admin (reward), 11 = Ads Manager only
        def is_admin(self, uid, scope=None):
            uid = int(uid)
            if scope is None:
                return uid in (9, 11)
            return {9: "reward", 11: "ads"}.get(uid) == scope

    directory = tempfile.TemporaryDirectory(prefix="clickmint-api-")
    try:
        root = Path(directory.name)
        store = JsonStore(str(root / "store.json"))
        ads = AdCampaignStore(root / "ads.sqlite3", enabled=True)
        api = AdminReadAPI(bot_token="token", owner_id=8, roles=ScopedRoles(),
                           channels=ChannelRegistry(store), marketplace=TaskMarketplace(root / "t.sqlite3"),
                           snapshots=PerformanceSnapshotRepository(root / "c.sqlite3"),
                           broadcasts=BroadcastQueue(root / "b.sqlite3"), ads=ads)
        now = str(int(time.time()))
        owner = signed("token", {"auth_date": now, "user": json.dumps({"id": 8})})
        network_admin = signed("token", {"auth_date": now, "user": json.dumps({"id": 9})})
        ads_manager = signed("token", {"auth_date": now, "user": json.dumps({"id": 11})})

        def denied(fn):
            try:
                fn()
            except AdminAuthorizationError:
                return
            raise AssertionError("authorization should have failed")

        denied(lambda: api.publish_ad_terms(ads_manager, "t"))            # owner only
        api.publish_ad_terms(owner, "Advertising terms v1")
        denied(lambda: api.create_ad_campaign(network_admin, "t", "Acme", "Guides", "x"))   # wrong scope
        cid = api.create_ad_campaign(ads_manager, "Launch", "Acme", "Guides", "hello")["campaign_id"]
        assert api.ads_overview(network_admin)["campaigns"][0]["status"] == "draft"   # read-only is fine
        api.ad_campaign_action(ads_manager, cid, "submit")
        denied(lambda: api.ad_campaign_action(ads_manager, cid, "approve", "self-approval"))   # review is owner-only
        try:
            api.ad_campaign_action(owner, cid, "approve", "")
        except AdPolicyError:
            pass
        else:
            raise AssertionError("approval without a reason accepted")
        api.ad_campaign_action(owner, cid, "approve", "disclosed, on-topic")
        try:
            api.ad_campaign_action(ads_manager, cid, "queue")
        except AdPolicyError as exc:
            assert "consented" in str(exc)
        else:
            raise AssertionError("queued with zero consent")
        ads.grant_consent("@willing", owner_id=42, terms_version=1)
        assert api.ad_campaign_action(ads_manager, cid, "queue")["new_deliveries"] == 1
        assert api.ad_campaign_action(ads_manager, cid, "pause")["status"] == "applied"
        ads.enabled = False
        try:
            api.ad_campaign_action(ads_manager, cid, "resume")
        except AdPolicyError as exc:
            assert "disabled" in str(exc)
        else:
            raise AssertionError("resumed while ads disabled")
    finally:
        directory.cleanup()


if __name__ == "__main__":
    test_authenticated_dashboard_is_read_only()
    print("PASS test_authenticated_dashboard_is_read_only")
    test_reports_appeals_and_owner_only_enforcement()
    print("PASS test_reports_appeals_and_owner_only_enforcement")
    test_ads_are_scoped_consent_gated_and_owner_reviewed()
    print("PASS test_ads_are_scoped_consent_gated_and_owner_reviewed")
