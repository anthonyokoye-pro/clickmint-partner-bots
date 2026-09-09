"""Single command validation entry point.

Install requirements first for complete bot wiring coverage:
    python -m pip install -r requirements.txt
    python run_all_tests.py
"""
from __future__ import annotations

import subprocess
import sys

TESTS = [
    "test_core.py", "test_governance.py", "test_bots.py", "simulate.py", "branding.py",
    "test_enforcement.py", "test_ad_campaigns.py", "test_human_verification.py", "test_broadcast_lifecycle.py",
    "test_platform_foundations.py", "test_task_marketplace.py", "test_performance_snapshots.py",
    "test_credibility.py", "test_economics.py", "test_mint_ledger.py", "test_db_backup.py",
    "test_integration_smoke.py", "test_admin_service.py", "test_admin_deploy.py",
    "test_admin_http.py", "test_admin_api.py", "test_admin_idempotency.py", "test_webapp_auth.py",
]


def main() -> int:
    for script in TESTS:
        print(f"\n=== {script} ===", flush=True)
        result = subprocess.run([sys.executable, script])
        if result.returncode:
            print(f"FAILED: {script} (exit {result.returncode})", flush=True)
            return result.returncode
    print("\nALL CLICKMINT TESTS PASSED", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
