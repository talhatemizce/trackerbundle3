from __future__ import annotations

import os
import json
import asyncio
from typing import Optional, Dict, Any
import httpx

from app import watchlist_store as ws
from app import ebay_client

SPAPI_BASE_URL = os.getenv("SPAPI_BASE_URL", "http://127.0.0.1/spapi/offers/top2")
TICK_SECONDS   = int(os.getenv("SCHED_TICK_SECONDS", "60"))
BATCH_LIMIT    = int(os.getenv("SCHED_BATCH_LIMIT", "4"))
HTTP_TIMEOUT   = float(os.getenv("SCHED_HTTP_TIMEOUT", "20"))
USER_AGENT     = os.getenv("SCHED_USER_AGENT", "trackerbundle-scheduler/1.0")

def _safe_json(text: str) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(text)
    except Exception:
        return None

async def fetch_for_item(client: httpx.AsyncClient, item: ws.WatchItem) -> None:
    if item.kind == "ebay_item":
        data = await ebay_client.browse_get_item(client, item.key)
        if isinstance(data, dict) and not data.get("error"):
            ws.mark_result(item.key, 200, data, None)
        else:
            ws.mark_result(item.key, 502, data if isinstance(data, dict) else None, data.get("error") if isinstance(data, dict) else "ebay_item_error", force_delay_minutes=10)
        return

    if item.kind == "ebay_sold":
        data = await ebay_client.finding_sold_stats(client, item.key)
        if isinstance(data, dict) and not data.get("error"):
            ws.mark_result(item.key, 200, data, None)
        else:
            ws.mark_result(item.key, 502, data if isinstance(data, dict) else None, data.get("error") if isinstance(data, dict) else "ebay_sold_error", force_delay_minutes=60)
        return

    if item.kind != "asin":
        ws.mark_result(item.key, None, None, "kind_not_supported_yet", force_delay_minutes=60)
        return

    try:
        r = await client.get(SPAPI_BASE_URL, params={"asin": item.key})
        data = _safe_json(r.text)

        if 200 <= r.status_code < 300 and isinstance(data, dict):
            ws.mark_result(item.key, r.status_code, data, None)
        else:
            ws.mark_result(
                item.key,
                r.status_code,
                data if isinstance(data, dict) else None,
                f"http_{r.status_code}",
                force_delay_minutes=10,
            )
    except Exception as e:
        ws.mark_result(item.key, None, None, f"exception:{type(e).__name__}", force_delay_minutes=10)

async def main_loop() -> None:
    ws.ensure_db()
    headers = {"User-Agent": USER_AGENT}

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, headers=headers) as client:
        while True:
            due = ws.due_items(BATCH_LIMIT)
            for item in due:
                await fetch_for_item(client, item)
            await asyncio.sleep(TICK_SECONDS)

def main() -> None:
    try:
        asyncio.run(main_loop())
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
