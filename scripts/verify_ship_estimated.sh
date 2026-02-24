#!/usr/bin/env bash
# Verify _shipping_estimated field correctness in alert history
# Run on VPS: bash ~/trackerbundle3/scripts/verify_ship_estimated.sh [isbn]
#
# What this proves:
#   ship_estimated=false → item had numeric shipping cost (e.g. $5.22) → correct
#   ship_estimated=true  → item had CALCULATED/UNKNOWN shipping → correct

ISBN="${1:-9781841721835}"
API="http://127.0.0.1:8000"

echo "=== 1) All history entries for ISBN: $ISBN ==="
curl -sS "$API/alerts/history?isbn=$ISBN&limit=50" \
  | jq '.entries[] | {ts, total, ship_estimated, condition, decision, match_quality}'

echo ""
echo "=== 2) ship_estimated breakdown across all history ==="
curl -sS "$API/alerts/history?limit=500" \
  | jq '
    .entries
    | group_by(.ship_estimated)
    | map({
        ship_estimated: .[0].ship_estimated,
        count: length,
        example_totals: [.[0:3][].total]
      })
  '

echo ""
echo "=== 3) Specific ISBN — latest entry full fields ==="
curl -sS "$API/alerts/history?isbn=$ISBN&limit=1" \
  | jq '.entries[0] | {isbn, total, ship_estimated, condition, ts}'

echo ""
echo "=== 4) Code verification: _shipping_estimated always set ==="
grep -n "_shipping_estimated" ~/trackerbundle3/app/ebay_client.py
echo "  ↑ Should see: item[\"_shipping_estimated\"] = ship_estimated  (unconditional)"

echo ""
echo "=== 5) Penalty in scorer ==="
grep -n "ship_estimated\|penalty" ~/trackerbundle3/app/scheduler_ebay.py | grep bonus
echo "  ↑ Should see: bonus += -2 if ship_estimated else 0"

echo ""
echo "=== 6) Inject test + check ship_estimated=false for fixed-ship entry ==="
echo "  (only run if you want to force a fresh entry)"
echo "  curl -sS -X POST $API/debug/inject-history | jq ."
echo "  Then re-run: curl -sS '$API/alerts/history?limit=1' | jq '.entries[0].ship_estimated'"
