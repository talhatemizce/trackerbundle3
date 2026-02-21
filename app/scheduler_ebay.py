"""
eBay ISBN scanner — runs as trackerbundle-ebay-scheduler.service
Usage: python -m app.scheduler_ebay

Scans each tracked ISBN against eBay Browse API (FIXED_PRICE listings).
Applies condition-based price limits from rules_store.
Sends compact Telegram alerts for BUY/OFFER decisions.
Deduplicates alerts via app/data/ebay_alerts_sent.json (atomic file).

Env vars (loaded from /etc/trackerbundle.env):
  EBAY_CLIENT_ID, EBAY_CLIENT_SECRET   Browse API OAuth2
  EBAY_APP_ID                          Finding API (used by suggested_price)
  EBAY_ENV                             "production" | "sandbox"
  TELEGRAM_BOT_TOKEN
  TELEGRAM_CHAT_ID
  ISBN_STORE                           path to isbns.json
  EBAY_SCAN_INTERVAL_SECONDS           seconds between full scans (default 300)
  EBAY_SCAN_BATCH_PAUSE_SECONDS        pause between ISBNs (default 3)
  EBAY_OFFER_MULTIPLIER                offer ceiling = limit * multiplier (default 1.30)
"""
from __future__ import annotations

import os
import json
import time
import asyncio
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from app.ebay_client import get_app_token, _browse_base  # token helpers
from app.rules_store import effective_limit, USED_CONDITIONS

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [ebay-sched] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
SCAN_INTERVAL   = int(os.getenv("EBAY_SCAN_INTERVAL_SECONDS", "300"))
BATCH_PAUSE     = float(os.getenv("EBAY_SCAN_BATCH_PAUSE_SECONDS", "3"))
OFFER_MULT      = float(os.getenv("EBAY_OFFER_MULTIPLIER", "1.30"))

ISBN_STORE      = Path(os.getenv("ISBN_STORE",
    "/home/ubuntu/trackerbundle3/app/data/isbns.json"))
DATA_DIR        = Path(__file__).resolve().parent / "data"
ALERTS_FILE     = DATA_DIR / "ebay_alerts_sent.json"

TELEGRAM_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT   = os.getenv("TELEGRAM_CHAT_ID", "")

# ── Condition normalisation ───────────────────────────────────────────────────
# Maps eBay conditionId (or partial conditionDisplayName) to rules_store keys.
_COND_ID_MAP: Dict[str, str] = {
    "1000": "brand_new",
    "1500": "brand_new",     # New other
    "1750": "brand_new",     # New with defects
    "2000": "like_new",
    "2500": "like_new",
    "3000": "very_good",
    "4000": "good",
    "5000": "acceptable",
    "6000": "acceptable",    # For parts / not working
    "7000": "acceptable",
}

_COND_NAME_MAP: Dict[str, str] = {
    "new":          "brand_new",
    "like new":     "like_new",
    "very good":    "very_good",
    "good":         "good",
    "acceptable":   "acceptable",
    "poor":         "acceptable",
}


def _map_condition(cond_id: Optional[str], cond_name: Optional[str]) -> str:
    if cond_id:
        mapped = _COND_ID_MAP.get(str(cond_id).strip())
        if mapped:
            return mapped
    if cond_name:
        for key, val in _COND_NAME_MAP.items():
            if key in cond_name.lower():
                return val
    return "good"  # safe default


# ── ISBN store (read-only here) ───────────────────────────────────────────────

def load_isbns() -> List[str]:
    try:
        if ISBN_STORE.exists():
            raw  = ISBN_STORE.read_text(encoding="utf-8").strip()
            data = json.loads(raw or "[]")
            if isinstance(data, list):
                return [str(x).strip() for x in data if str(x).strip()]
    except Exception as exc:
        log.warning("load_isbns error: %s", exc)
    return []


# ── Alert dedup (atomic check-and-mark) ──────────────────────────────────────
_dedup_lock = asyncio.Lock()


def _read_alerts_unsafe() -> Dict[str, Any]:
    try:
        if ALERTS_FILE.exists():
            raw = ALERTS_FILE.read_text(encoding="utf-8").strip()
            return json.loads(raw or "{}")
    except Exception:
        pass
    return {}


def _write_alerts_unsafe(data: Dict[str, Any]) -> None:
    import os as _os
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = ALERTS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    _os.replace(tmp, ALERTS_FILE)


async def _should_alert(isbn: str, item_id: str) -> bool:
    """Return True and mark as sent (atomic). False if already sent."""
    async with _dedup_lock:
        sent = _read_alerts_unsafe()
        key  = f"{isbn}::{item_id}"
        if key in sent:
            return False
        sent[key] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        _write_alerts_unsafe(sent)
        return True


# ── eBay Browse API search ────────────────────────────────────────────────────

async def _browse_search_isbn(
    client: httpx.AsyncClient,
    isbn: str,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """
    Search eBay Browse API for FIXED_PRICE book listings by ISBN.
    Returns list of normalised items:
      {item_id, title, url, condition, condition_id, item_price, ship, total,
       currency, make_offer_enabled}
    All prices are floats.
    """
    token = await get_app_token(client)
    base  = _browse_base()
    url   = f"{base}/buy/browse/v1/item_summary/search"

    params = {
        "q":              isbn,
        "category_ids":   "267",
        "filter":         "buyingOptions:{FIXED_PRICE}",
        "limit":          str(limit),
        "fieldgroups":    "MATCHING_ITEMS",
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
        "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
    }

    try:
        r = await client.get(url, params=params, headers=headers, timeout=20)
    except Exception as exc:
        log.error("browse_search network error for isbn=%s: %s", isbn, exc)
        return []

    if r.status_code != 200:
        log.warning("browse_search status=%s for isbn=%s", r.status_code, isbn)
        return []

    try:
        j = r.json()
    except Exception:
        return []

    results: List[Dict[str, Any]] = []
    for it in (j.get("itemSummaries") or []):
        item_id    = it.get("itemId", "")
        title      = it.get("title", "")
        item_url   = it.get("itemWebUrl", "")
        cond_id    = (it.get("condition") or {}).get("conditionId")
        cond_name  = (it.get("condition") or {}).get("conditionDisplayName")
        condition  = _map_condition(cond_id, cond_name)

        buying_opts = it.get("buyingOptions") or []
        make_offer  = "BEST_OFFER" in buying_opts

        price_struct = it.get("price") or {}
        try:
            item_price = float(price_struct.get("value") or 0)
        except Exception:
            item_price = 0.0
        currency = price_struct.get("currency", "USD")

        ship = 0.0
        for sopt in (it.get("shippingOptions") or []):
            sc = (sopt.get("shippingCost") or {}).get("value")
            if sc is not None:
                try:
                    v = float(sc)
                    ship = min(ship, v) if ship != 0 else v
                except Exception:
                    pass

        total = round(item_price + ship, 2)

        results.append({
            "item_id":            item_id,
            "title":              title,
            "url":                item_url,
            "condition":          condition,
            "condition_id":       cond_id,
            "condition_name":     cond_name,
            "item_price":         item_price,
            "ship":               ship,
            "total":              total,
            "currency":           currency,
            "make_offer_enabled": make_offer,
        })

    return results


# ── Telegram notification ─────────────────────────────────────────────────────

async def _send_telegram(text: str) -> None:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        log.debug("Telegram not configured, skipping notification")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            await c.post(url, json={
                "chat_id":    TELEGRAM_CHAT,
                "text":       text,
                "parse_mode": "HTML",
            })
    except Exception as exc:
        log.warning("Telegram send error: %s", exc)


def _fmt_alert(isbn: str, item: Dict[str, Any], decision: str) -> str:
    """Compact Telegram message — no decimals, no verbose JSON."""
    cond   = (item.get("condition_name") or item.get("condition") or "").title()
    price  = int(round(item["item_price"]))
    ship   = int(round(item["ship"]))
    total  = int(round(item["total"]))
    title  = (item.get("title") or "")[:60]
    url    = item.get("url", "")
    emoji  = "🛒" if decision == "BUY" else "🤝"

    lines = [
        f"{emoji} <b>{decision}</b> — ISBN {isbn}",
        f"📚 {title}",
        f"🏷 Condition: {cond}",
        f"💵 ${price} + ship ${ship} = <b>${total}</b>",
    ]
    if url:
        lines.append(f'<a href="{url}">View listing</a>')
    return "\n".join(lines)


# ── Core scan logic ───────────────────────────────────────────────────────────

async def scan_isbn(client: httpx.AsyncClient, isbn: str) -> int:
    """Scan one ISBN. Returns count of alerts fired."""
    listings = await _browse_search_isbn(client, isbn)
    if not listings:
        return 0

    alerts_fired = 0
    for item in listings:
        condition = item["condition"]
        total     = item["total"]

        rule        = effective_limit(isbn, condition)
        price_limit = float(rule.get("limit") or 0)

        if price_limit <= 0:
            continue

        decision = None
        if total <= price_limit:
            decision = "BUY"
        elif item["make_offer_enabled"] and total <= price_limit * OFFER_MULT:
            decision = "OFFER"

        if decision is None:
            continue

        item_id = item["item_id"]
        if not item_id:
            continue

        if not await _should_alert(isbn, item_id):
            continue

        log.info("ALERT %s isbn=%s item=%s total=%s limit=%s",
                 decision, isbn, item_id, total, price_limit)

        msg = _fmt_alert(isbn, item, decision)
        await _send_telegram(msg)
        alerts_fired += 1

    return alerts_fired


# ── Main loop ─────────────────────────────────────────────────────────────────

async def main_loop() -> None:
    log.info("eBay scheduler starting (interval=%ss, offer_mult=%.2f)",
             SCAN_INTERVAL, OFFER_MULT)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Token lock mirroring architecture rule: token refresh guarded by asyncio.Lock
    # (handled inside get_app_token via ebay_client module)

    while True:
        isbns = load_isbns()
        log.info("Scan cycle: %d ISBNs", len(isbns))

        if isbns:
            async with httpx.AsyncClient(timeout=25) as client:
                for isbn in isbns:
                    try:
                        fired = await scan_isbn(client, isbn)
                        if fired:
                            log.info("isbn=%s: %d alert(s) fired", isbn, fired)
                    except Exception as exc:
                        log.error("isbn=%s scan error: %s", isbn, exc)
                    await asyncio.sleep(BATCH_PAUSE)

        log.info("Scan cycle complete. Sleeping %ss", SCAN_INTERVAL)
        await asyncio.sleep(SCAN_INTERVAL)


def main() -> None:
    try:
        asyncio.run(main_loop())
    except KeyboardInterrupt:
        log.info("Shutting down")


if __name__ == "__main__":
    main()
