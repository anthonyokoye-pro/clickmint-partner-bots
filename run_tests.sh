#!/usr/bin/env bash
# Run every CLICKMINT check exactly as the audit does. No network, no Telegram.
#   ./run_tests.sh
set -euo pipefail

echo "== compile =="            && python3 -m py_compile *.py
echo "== engine =="             && python3 test_core.py
echo "== governance/roles =="   && python3 test_governance.py
echo "== bot wiring =="         && python3 test_bots.py
echo "== branding limits =="    && python3 branding.py --check
echo "== dry run (scripted) ==" && python3 simulate.py --quiet
echo
echo "ALL CHECKS PASSED"
