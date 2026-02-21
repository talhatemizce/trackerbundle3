from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Tuple

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.config import get_settings
from app import isbn_store
from app.rules_store import get_rule
from app import run_state
from app.ebay_client import browse_search_isbn, finding_sold_stats, normalize_condition, item_total_price
from app.alert_store import check_and_mark

logger = logging.getLogger("trackerbundle.scheduler_ebay")


def _build_limits() -> Dict[str, float]:
    s = get_settings()
    base = float(s.default_good_limit)
    return {
        "brand_new": float(s.default_new_limit),
        "like_new": round(base * 1.15, 2),
        "very_good": round(base * 1.10, 2),
        "good": base,
        "acceptable": round(base * 0.80, 2),
        "used_all": base,
    }


async def _send_telegram(message: str) -> bool:
    s = get_settings()
    if not s.telegram_bot_token or not s.telegram_chat_id:
        logger.warning("Telegram token/chat_id missing")
        return False

    url = f"https://api.telegram.org/bot{s.telegram_bot_token}/sendMessage"
    payload = {
        "chat_id": s.telegram_chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:
        async with httpx.AsyncClient(timeout=12) as c:
            r = await c.post(url, json=payload)
            r.raise_for_status()
            return True
    except Exception:
        logger.exception("Telegram sendMessage failed")
        return False


def _format_message(
    isbn: str,
    item: Dict[str, Any],
    bucket: str,
    total: float,
    limit: float,
    sold_avg: int | None = None,
    sold_count: int | None = None,
) -> str:
    title = (item.get("title") or "")[:90]
    url = item.get("itemWebUrl") or ""
    make_offer = "BEST_OFFER" in (item.get("buyingOptions") or [])

    label = {
        "brand_new": "New",
        "like_new": "Like New",
        "very_good": "Very Good",
        "good": "Good",
        "acceptable": "Acceptable",
        "used_all": "Used",
    }.get(bucket, bucket)

    total_i = int(round(total))
    limit_i = int(round(limit))

    # BUY / OFFER / SKIP kararı
    if make_offer:
        decision = "OFFER"
        offer_target = int(round(limit / float(get_settings().make_offer_multiplier)))
        decision_str = f"🟡 OFFER ~${offer_target}"
    else:
        decision = "BUY"
        decision_str = "🟢 BUY"

    offer_str = " · Make Offer" if make_offer else ""

    msg = (
        f"📚 <b>{title}</b>\n"
        f"ISBN: {isbn} | {label}{offer_str}\n"
        f"Total: <b>${total_i}</b> (limit ${limit_i}) → {decision_str}\n"
    )

    # Sold stats satırı
    if sold_avg is not None:
        sold_str = f"Sold avg: ${sold_avg}"
        if sold_count is not None:
            sold_str += f" ({sold_count} sold)"

        # overpriced uyarısı: aktif fiyat ucuz ama sold avg çok düşükse
        if sold_avg < total_i:
            sold_str += " ⚠️ sold avg < listing"
        msg += f"{sold_str}\n"

    if url:
        msg += f'<a href="{url}">eBay</a>'
    return msg


@retry(
    reraise=True,
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, min=2, max=20),
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.ConnectError, httpx.HTTPStatusError)),
)
async def _fetch(client: httpx.AsyncClient, isbn: str) -> List[Dict[str, Any]]:
    return await browse_search_isbn(client, isbn, limit=50)


def _pick_candidates_under_limit(items: List[Dict[str, Any]], limits: Dict[str, float]) -> List[Tuple[Dict[str, Any], str, float, float]]:
    """
    Returns list of (item, bucket, total, limit) only for items where total <= limit.
    Sort by total ascending, keep only 2 cheapest.
    """
    s = get_settings()
    out: List[Tuple[Dict[str, Any], str, float, float]] = []

    for it in items:
        total = item_total_price(it)
        if total is None:
            continue

        bucket = normalize_condition(it.get("condition"), it.get("conditionId"))
        limit = float(limits.get(bucket, limits["good"]))

        if "BEST_OFFER" in (it.get("buyingOptions") or []):
            limit = float(round(limit * float(s.make_offer_multiplier), 2))

        if total <= limit:
            out.append((it, bucket, float(total), float(limit)))

    out.sort(key=lambda x: x[2])
    return out[:2]


async def _fetch_sold(client: httpx.AsyncClient, isbn: str) -> Dict[str, Any]:
    """Sold stats çek — hata olursa boş dict döndür (scheduler durmasın)."""
    try:
        return await finding_sold_stats(client, isbn)
    except Exception:
        logger.warning("ISBN %s sold stats fetch failed (non-fatal)", isbn)
        return {}


async def _check_isbn(client: httpx.AsyncClient, isbn: str, limits: Dict[str, float]) -> int:
    sent = 0
    try:
        items = await _fetch(client, isbn)
    except Exception:
        logger.exception("ISBN %s fetch failed", isbn)
        return 0

    candidates = _pick_candidates_under_limit(items, limits)
    logger.info("isbn=%s candidates=%d (limit altı)", isbn, len(candidates))

    if not candidates:
        return 0

    # Sold stats'ı sadece candidate varsa çek (API çağrısını boşa harcamayalım)
    sold = await _fetch_sold(client, isbn)
    sold_overall_avg = sold.get("sold_avg")
    sold_overall_count = sold.get("sold_count")
    sold_by_cond = sold.get("by_condition", {})

    for it, bucket, total, limit in candidates:
        item_id = str(it.get("itemId") or "")
        if not item_id:
            continue

        already = check_and_mark(isbn, item_id)
        if already:
            logger.info("isbn=%s item=%s skip=already_notified", isbn, item_id)
            continue

        # Condition bazlı sold avg varsa onu kullan, yoksa genel avg
        cond_stats = sold_by_cond.get(bucket, {})
        avg_for_msg = cond_stats.get("avg") if cond_stats.get("avg") is not None else sold_overall_avg
        count_for_msg = cond_stats.get("count") if cond_stats.get("count") is not None else sold_overall_count

        msg = _format_message(isbn, it, bucket, total, limit, sold_avg=avg_for_msg, sold_count=count_for_msg)
        ok = await _send_telegram(msg)
        if ok:
            sent += 1

    return sent


def _interval_for_isbn(isbn: str) -> int:
    """Per-ISBN interval. Missing/None -> default tick."""
    r = get_rule(isbn)
    if not r:
        return int(get_settings().sched_tick_seconds)
    sec = getattr(r, "interval_seconds", None)
    if sec is None:
        return int(get_settings().sched_tick_seconds)
    try:
        sec_i = int(sec)
        if sec_i <= 0:
            return int(get_settings().sched_tick_seconds)
        return sec_i
    except Exception:
        return int(get_settings().sched_tick_seconds)


async def run_once() -> None:
    s = get_settings()
    isbns = isbn_store.list_isbns()
    if not isbns:
        logger.info("Watchlist empty")
        return

    now = time.time()
    due_isbns: List[str] = []
    for isbn in isbns:
        interval = _interval_for_isbn(isbn)
        if run_state.due(isbn, interval, now=now):
            due_isbns.append(isbn)

    logger.info("Checking %d/%d ISBN (due)", len(due_isbns), len(isbns))
    if not due_isbns:
        return

    limits = _build_limits()
    async with httpx.AsyncClient(timeout=20) as client:
        for isbn in due_isbns:
            sent = await _check_isbn(client, isbn, limits)
            run_state.set_last_run(isbn, ts=now)
            logger.info("isbn=%s alerts=%d", isbn, sent)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    s = get_settings()
    tick = int(s.sched_tick_seconds)
    logger.info("Scheduler start tick=%ss ebay_env=%s", tick, s.ebay_env)

    while True:
        try:
            await run_once()
        except Exception:
            logger.exception("run_once crash")
        await asyncio.sleep(tick)


if __name__ == "__main__":
    asyncio.run(main())
