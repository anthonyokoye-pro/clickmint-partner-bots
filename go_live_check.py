"""Offline production go-live checklist runner."""
from __future__ import annotations

import json
from pathlib import Path

from admin_deploy import admin_preflight


def run_go_live_check(root: str | Path, *, bot_token: str, owner_id: str,
                      allowed_origins: list[str], database_paths: dict[str, str | Path],
                      ads_enabled: bool = False, ads_terms_published: bool = False,
                      onboarding_url: str = "") -> dict:
    root = Path(root)
    checks = {}
    errors = admin_preflight(root, bot_token=bot_token, owner_id=owner_id,
                             allowed_origins=allowed_origins)
    checks["admin_preflight"] = {"ok": not errors, "errors": errors}
    checks["frontend"] = {"ok": all((root / item).is_file() for item in (
        "admin_web/index.html", "admin_web/app.js", "admin_web/styles.css"))}
    checks["database_paths"] = {
        name: {"configured": bool(str(path)), "path": str(path)}
        for name, path in database_paths.items()
    }
    checks["financial_features"] = {
        "revenue": "disabled", "deposits": "disabled", "withdrawals": "disabled",
        "external_payouts": "disabled",
    }
    # Advertising may only be ON when versioned terms exist for consent to reference.
    checks["advertising"] = {
        "enabled": bool(ads_enabled), "terms_published": bool(ads_terms_published),
        "ok": (not ads_enabled) or ads_terms_published,
        "note": "consent-gated; ADS_ENABLED without published terms is a misconfiguration",
    }
    # Token onboarding: the Mini App must be HTTPS and on an allowed origin; an
    # unset URL is a WARNING (in-chat fallback still works) rather than a failure.
    url_ok = (not onboarding_url) or (onboarding_url.startswith("https://") and
                                      any(onboarding_url.startswith(o.rstrip("/") + "/") or onboarding_url == o
                                          for o in allowed_origins))
    checks["token_onboarding"] = {
        "configured": bool(onboarding_url), "ok": url_ok,
        "note": ("members paste bot tokens on the HTTPS Mini App" if onboarding_url else
                 "ONBOARDING_WEBAPP_URL unset — members will paste tokens into chat (weaker); set it before launch"),
    }
    return {"ok": checks["admin_preflight"]["ok"] and checks["frontend"]["ok"] and checks["advertising"]["ok"]
            and checks["token_onboarding"]["ok"],
            "checks": checks}


def main() -> int:
    import config
    from ad_campaigns import AdCampaignStore
    ads = AdCampaignStore(config.ADS_DB_PATH, enabled=config.ADS_ENABLED)
    report = run_go_live_check(
        Path(__file__).parent, bot_token=config.ADMIN_BOT_TOKEN,
        ads_enabled=config.ADS_ENABLED, ads_terms_published=ads.current_terms() is not None,
        owner_id=str(config.OWNER_USER_ID),
        allowed_origins=config.ADMIN_ALLOWED_ORIGINS,
        onboarding_url=config.ONBOARDING_WEBAPP_URL,
        database_paths={
            "mint": config.MINT_DB_PATH,
            "tasks": config.TASK_DB_PATH,
            "broadcast": config.BROADCAST_DB_PATH,
            "credibility": config.CREDIBILITY_DB_PATH,
            "admin_api": config.ADMIN_API_DB_PATH,
            "enforcement": config.ENFORCEMENT_DB_PATH,
            "ads": config.ADS_DB_PATH,
            "verification": config.VERIFICATION_DB_PATH,
        },
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
