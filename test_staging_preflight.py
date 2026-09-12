import tempfile
from pathlib import Path
from staging_preflight import validate


def test_isolated_staging_configuration_passes():
    with tempfile.TemporaryDirectory(prefix="clickmint-staging-") as directory:
        root = Path(directory)
        values = {
            "ENVIRONMENT": "staging",
            "REWARD_BOT_TOKEN": "111111:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
            "PARTNER_BOT_TOKEN": "222222:BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB",
            "ADMIN_BOT_TOKEN": "333333:CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC",
            "OWNER_USER_ID": "123",
            "CLICKMINT_CREDENTIAL_KEY": "x" * 40,
            "ADMIN_ALLOWED_ORIGINS": "https://staging.example",
            "ONBOARDING_WEBAPP_URL": "https://staging.example/onboarding_web/",
            "STORE_DIR": str(root),
        }
        for name in ("MINT_DB_PATH", "TASK_DB_PATH", "BROADCAST_DB_PATH", "VERIFICATION_DB_PATH", "PLATFORM_DB_PATH", "CREDIBILITY_DB_PATH", "ENFORCEMENT_DB_PATH", "ADS_DB_PATH", "ADMIN_API_DB_PATH", "WORKER_DB_PATH"):
            values[name] = str(root / (name.lower() + ".sqlite3"))
        for key in ("DEPOSITS_ENABLED", "WITHDRAWALS_ENABLED", "CRYPTO_PAYMENTS_ENABLED", "FIAT_PAYMENTS_ENABLED", "TELEGRAM_STARS_ENABLED", "BOOST_PURCHASES_ENABLED", "EXTERNAL_PAYOUTS_ENABLED", "CURRENCY_CONVERSION_ENABLED"):
            values[key] = "false"
        assert validate(values) == []


def test_staging_rejects_shared_or_insecure_configuration():
    values = {"ENVIRONMENT": "production", "REWARD_BOT_TOKEN": "same", "PARTNER_BOT_TOKEN": "same", "ADMIN_BOT_TOKEN": "same", "CLICKMINT_CREDENTIAL_KEY": "short", "ADMIN_ALLOWED_ORIGINS": "http://localhost", "ONBOARDING_WEBAPP_URL": "http://localhost", "STORE_DIR": "/tmp/data", "MINT_DB_PATH": "/tmp/mint.sqlite3"}
    assert validate(values)


if __name__ == "__main__":
    test_isolated_staging_configuration_passes()
    test_staging_rejects_shared_or_insecure_configuration()
    print("PASS staging preflight tests")
