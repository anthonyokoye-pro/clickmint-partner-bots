"""Launch-scope guard for the non-financial MVP."""
from __future__ import annotations

FINANCIAL_FLAGS = (
    "DEPOSITS_ENABLED", "WITHDRAWALS_ENABLED", "CRYPTO_PAYMENTS_ENABLED",
    "FIAT_PAYMENTS_ENABLED", "TELEGRAM_STARS_ENABLED", "BOOST_PURCHASES_ENABLED",
    "EXTERNAL_PAYOUTS_ENABLED", "CURRENCY_CONVERSION_ENABLED",
)


def financial_scope_report(config) -> dict:
    flags = {name: bool(getattr(config, name, False)) for name in FINANCIAL_FLAGS}
    return {"mode": "non_financial_mvp", "financial_features": flags,
            "ok": not any(flags.values())}


def assert_nonfinancial_scope(config) -> dict:
    report = financial_scope_report(config)
    if not report["ok"]:
        enabled = [name for name, value in report["financial_features"].items() if value]
        raise RuntimeError("financial features are disabled for this launch scope: " + ", ".join(enabled))
    return report
