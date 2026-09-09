from pathlib import Path
from go_live_check import run_go_live_check


def test_go_live_report_preserves_financial_boundaries():
    report = run_go_live_check(Path(__file__).parent, bot_token="x" * 40,
                               owner_id="123", allowed_origins=["https://admin.example"],
                               database_paths={"mint": "store/mint.sqlite3"})
    assert report["ok"]
    assert report["checks"]["financial_features"]["withdrawals"] == "disabled"
    assert report["checks"]["frontend"]["ok"]


def test_ads_enabled_without_terms_fails_go_live():
    kwargs = dict(bot_token="x" * 40, owner_id="123", allowed_origins=["https://admin.example"],
                  database_paths={"mint": "store/mint.sqlite3"})
    bad = run_go_live_check(Path(__file__).parent, ads_enabled=True, ads_terms_published=False, **kwargs)
    assert not bad["ok"] and not bad["checks"]["advertising"]["ok"]
    good = run_go_live_check(Path(__file__).parent, ads_enabled=True, ads_terms_published=True, **kwargs)
    assert good["ok"]


if __name__ == "__main__":
    test_go_live_report_preserves_financial_boundaries()
    print("PASS test_go_live_report_preserves_financial_boundaries")
    test_ads_enabled_without_terms_fails_go_live()
    print("PASS test_ads_enabled_without_terms_fails_go_live")
