#!/usr/bin/env bash
# Run the complete CLICKMINT check suite in the dependency-aware environment.
set -euo pipefail

PYTHON="${CLICKMINT_PYTHON:-python3}"
if ! "$PYTHON" -c 'import aiogram' >/dev/null 2>&1; then
  if [ -x /tmp/cmvenv/bin/python ] && /tmp/cmvenv/bin/python -c 'import aiogram' >/dev/null 2>&1; then
    PYTHON=/tmp/cmvenv/bin/python
  else
    echo "aiogram is required for bot wiring tests." >&2
    echo "Install dependencies with: python3 -m pip install -r requirements.txt" >&2
    echo "Or set CLICKMINT_PYTHON to a prepared environment." >&2
    exit 2
  fi
fi

echo "== compile ==" && "$PYTHON" -m py_compile *.py
echo "== complete test runner ==" && "$PYTHON" run_all_tests.py
echo "== branding limits ==" && "$PYTHON" branding.py --check
echo "== offline simulation ==" && "$PYTHON" simulate.py --quiet
echo "ALL CHECKS PASSED"
