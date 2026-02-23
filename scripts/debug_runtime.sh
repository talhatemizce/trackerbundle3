#!/usr/bin/env bash
# TrackerBundle3 — Runtime path diagnosis
# Run on VPS: bash ~/trackerbundle3/scripts/debug_runtime.sh
set -euo pipefail

HR="─────────────────────────────────────────────"

echo "$HR"
echo "1) systemd service definition"
echo "$HR"
sudo systemctl cat trackerbundle-api.service 2>/dev/null || echo "[WARN] service not found or no sudo"

echo ""
echo "$HR"
echo "2) Running process (WorkingDirectory + ExecStart reality)"
echo "$HR"
UPID=$(pgrep -f 'uvicorn app.main:app' | head -n1 || true)
if [ -n "$UPID" ]; then
  echo "PID: $UPID"
  echo "CMD: $(ps -o cmd= -p $UPID)"
  echo "CWD: $(readlink -f /proc/$UPID/cwd)"
  echo "EXE: $(readlink -f /proc/$UPID/exe)"
  # Show actual app/main.py being used
  ACTUAL_MAIN="$(readlink -f /proc/$UPID/cwd)/app/main.py"
  if [ -f "$ACTUAL_MAIN" ]; then
    echo "RUNTIME app/main.py: $ACTUAL_MAIN"
    echo "  size : $(wc -c < "$ACTUAL_MAIN") bytes"
    echo "  mtime: $(stat -c '%y' "$ACTUAL_MAIN")"
    echo "  telemetry lines: $(grep -c 'telemetry' "$ACTUAL_MAIN" || echo 0)"
  else
    echo "[WARN] $ACTUAL_MAIN not found"
  fi
else
  echo "[WARN] uvicorn process not found — service may be down"
fi

echo ""
echo "$HR"
echo "3) openapi.json health"
echo "$HR"
curl -sS -D- http://127.0.0.1:8000/openapi.json -o /tmp/openapi_check.json --max-time 5 2>&1 | head -20
echo ""
echo "Response body size: $(wc -c < /tmp/openapi_check.json) bytes"
if [ "$(wc -c < /tmp/openapi_check.json)" -gt 100 ]; then
  python3 -c "import json,sys; d=json.load(open('/tmp/openapi_check.json')); print('  JSON OK — paths:', len(d.get('paths',{})))"
else
  echo "  [WARN] Body too small — likely empty or error page"
  cat /tmp/openapi_check.json
fi

echo ""
echo "$HR"
echo "4) Repo vs runtime diff check"
echo "$HR"
REPO_DIR="$(dirname "$(dirname "$(realpath "$0")")")"
echo "Repo dir : $REPO_DIR"
echo "Repo main.py mtime: $(stat -c '%y' "$REPO_DIR/app/main.py")"
echo "Repo git HEAD: $(cd "$REPO_DIR" && git log --oneline -1)"

if [ -n "${ACTUAL_MAIN:-}" ] && [ "$ACTUAL_MAIN" != "$REPO_DIR/app/main.py" ]; then
  echo ""
  echo "[ACTION NEEDED] Runtime uses different directory than repo!"
  echo "  Repo   : $REPO_DIR/app/main.py"
  echo "  Runtime: $ACTUAL_MAIN"
  echo ""
  echo "Fix options:"
  echo "  A) Copy repo to runtime dir:  sudo cp -r $REPO_DIR/app $( readlink -f /proc/$UPID/cwd)/"
  echo "  B) Update systemd WorkingDirectory to: $REPO_DIR"
else
  echo "Repo and runtime appear to be same directory — git pull + restart should suffice"
fi

echo ""
echo "$HR"
echo "5) data dir + telemetry file"
echo "$HR"
for D in "$REPO_DIR/data" "/var/lib/trackerbundle3" "/home/ubuntu/trackerbundle3/data" "/root/trackerbundle3/data"; do
  [ -d "$D" ] && echo "  Found data dir: $D ($(ls "$D" | wc -l) files)" && ls "$D"
done
echo "Searching for link_telemetry.jsonl..."
find / -name "link_telemetry.jsonl" 2>/dev/null | head -5 || echo "  Not found anywhere"

echo ""
echo "$HR"
echo "SUMMARY"
echo "$HR"
echo "Next step: if runtime dir != repo dir, run:"
echo "  sudo systemctl edit trackerbundle-api.service  # set WorkingDirectory"
echo "  sudo systemctl daemon-reload && sudo systemctl restart trackerbundle-api.service"
