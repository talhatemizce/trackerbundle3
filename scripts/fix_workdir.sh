#!/usr/bin/env bash
# TrackerBundle3 — Fix runtime path mismatch
# Usage: sudo bash ~/trackerbundle3/scripts/fix_workdir.sh
# Safe to run multiple times (idempotent).
set -euo pipefail

REPO_DIR="$(dirname "$(dirname "$(realpath "$0")")")"
SERVICE="trackerbundle-api.service"

echo "Repo dir: $REPO_DIR"
echo "Service : $SERVICE"
echo ""

# 1. Show current WorkingDirectory
CURRENT_WD=$(sudo systemctl show "$SERVICE" --property=WorkingDirectory --value 2>/dev/null || echo "unknown")
echo "Current WorkingDirectory: $CURRENT_WD"

if [ "$CURRENT_WD" = "$REPO_DIR" ]; then
  echo "WorkingDirectory is already correct."
  echo "Checking if just a restart is needed..."
  sudo systemctl restart "$SERVICE"
  sleep 2
  sudo systemctl status "$SERVICE" --no-pager -l | head -20
  exit 0
fi

# 2. Create drop-in override
DROPIN_DIR="/etc/systemd/system/${SERVICE}.d"
sudo mkdir -p "$DROPIN_DIR"

sudo tee "$DROPIN_DIR/workdir.conf" > /dev/null << EOF
[Service]
WorkingDirectory=$REPO_DIR
EOF

echo "Created drop-in: $DROPIN_DIR/workdir.conf"
cat "$DROPIN_DIR/workdir.conf"

# 3. Reload + restart
sudo systemctl daemon-reload
sudo systemctl restart "$SERVICE"
sleep 2

echo ""
echo "=== Service status after fix ==="
sudo systemctl status "$SERVICE" --no-pager -l | head -25

echo ""
echo "=== Verify telemetry endpoint ==="
sleep 1
curl -s -X POST http://127.0.0.1:8000/telemetry/link-broken \
  -H "Content-Type: application/json" \
  -d '{"isbn":"TEST","url":"http://test","context":"fix_script","build_id":"manual_test"}' \
  | python3 -m json.tool

echo ""
echo "=== Check jsonl created ==="
find "$REPO_DIR/data" -name "link_telemetry.jsonl" 2>/dev/null && \
  tail -1 "$REPO_DIR/data/link_telemetry.jsonl" || echo "File not found in $REPO_DIR/data"

echo ""
echo "=== openapi check ==="
curl -s http://127.0.0.1:8000/openapi.json -o /tmp/oa.json --max-time 5
python3 -c "import json; d=json.load(open('/tmp/oa.json')); print(f'OK — {len(d[\"paths\"])} paths')" 2>/dev/null || \
  echo "[FAIL] openapi still broken — $(wc -c < /tmp/oa.json) bytes: $(cat /tmp/oa.json | head -c 100)"
