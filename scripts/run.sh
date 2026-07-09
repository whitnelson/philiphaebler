#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
  .venv/bin/pip install -r requirements.txt
  .venv/bin/playwright install chromium
fi

echo "=== Archiving site ==="
.venv/bin/python scripts/archive.py

echo ""
echo "=== Starting local server on :8765 ==="
.venv/bin/python -m http.server 8765 --directory site &
SERVER_PID=$!
trap 'kill $SERVER_PID 2>/dev/null || true' EXIT
sleep 1

echo ""
echo "=== Testing archived site ==="
.venv/bin/python scripts/test_site.py
TEST_EXIT=$?

echo ""
echo "Archive complete. Upload the 'site/' directory to your web server."
echo "Local preview: python -m http.server 8765 --directory site"
exit $TEST_EXIT
