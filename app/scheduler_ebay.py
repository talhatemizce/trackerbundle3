from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Tuple

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.config import get_settings
from app import isbn_store
from app.rules_store import get_rule, effective_limit
from app import run_state
from app.ebay_client import browse_search_isbn, finding_sold_stats, normalize_condition, item_total_price
from app.alert_store import check_and_mark
from app import alert_history_store
from app import sold_stats_store

logger = logging.getLogger("trackerbundle.scheduler_ebay")


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
    ship_estimated: bool = False,
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

    ship_note = " · est. ship" if ship_estimated else ""
    msg = (
        f"📚 <b>{title}</b>\n"
        f"ISBN: {isbn} | {label}{offer_str}\n"
        f"Total: <b>${total_i}</b>{ship_note} (limit ${limit_i}) → {decision_str}\n"
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


def _pick_candidates_under_limit(items: List[Dict[str, Any]], isbn: str) -> List[Tuple[Dict[str, Any], str, float, float]]:
    """
    Returns list of (item, bucket, total, limit) only for items where total <= limit.
    Uses per-ISBN price overrides from rules_store (effective_limit), falling back to
    global defaults.  Sort by total ascending, keep only 2 cheapest.
    """
    s = get_settings()
    out: List[Tuple[Dict[str, Any], str, float, float]] = []

    calc_est = s.calculated_ship_estimate_usd if s.calculated_ship_estimate_usd > 0 else None
    for it in items:
        item_id = (it.get("itemId") or "?")[:20]
        price_val = (it.get("price") or {}).get("value", "?")
        ship_opts = it.get("shippingOptions") or []
        ship_type = ((ship_opts[0].get("shippingCostType") or ship_opts[0].get("shippingServiceType") or ship_opts[0].get("shippingType") or "?") if ship_opts else "none").upper()
        cid = it.get("conditionId", "?")

        total = item_total_price(it, calc_ship_est=calc_est)
        if total is None:
            logger.info(
                "EXCLUDE isbn=%s item=%s price=%s ship=%s condId=%s → total=None (CALCULATED/unknown ship)",
                isbn, item_id, price_val, ship_type, cid,
            )
            continue

        bucket = normalize_condition(it.get("condition"), it.get("conditionId"))
        lim_info = effective_limit(isbn, bucket)
        limit = float(lim_info["limit"])
        has_offer = "BEST_OFFER" in (it.get("buyingOptions") or [])

        if has_offer:
            limit = float(round(limit * float(s.make_offer_multiplier), 2))

        if total <= limit:
            logger.info(
                "INCLUDE isbn=%s item=%s total=%.2f limit=%.2f bucket=%s offer=%s",
                isbn, item_id, total, limit, bucket, has_offer,
            )
            out.append((it, bucket, float(total), float(limit)))
        else:
            logger.info(
                "EXCLUDE isbn=%s item=%s total=%.2f > limit=%.2f bucket=%s offer=%s",
                isbn, item_id, total, limit, bucket, has_offer,
            )

    out.sort(key=lambda x: x[2])
    return out[:2]


async def _fetch_sold(client: httpx.AsyncClient, isbn: str) -> Dict[str, Any]:
    """
    Sold stats çek — hata olursa boş dict döndür (scheduler durmasın).
    Başarılıysa sold_stats_store'a snapshot yazar (365d/3yr birikim için).
    """
    try:
        result = await finding_sold_stats(client, isbn)
    except Exception:
        logger.warning("ISBN %s sold stats fetch failed (non-fatal)", isbn)
        return {}

    # ── Accumulator'a yaz (sold_avg hesaplaması için geçmiş veri biriktir) ───
    # finding_sold_stats → tüm condition'lar için toplam + condition breakdown döner
    # by_condition breakdown'u ayrı ayrı saklıyoruz (new/used kondisyon trendsı için)
    try:
        all_totals = _rebuild_totals_from_stats(result)
        if all_totals:
            sold_stats_store.append_snapshot(isbn, 90, None, all_totals)
        # Condition breakdown
        for bucket, stats in (result.get("by_condition") or {}).items():
            cond_key = "new" if bucket == "brand_new" else "used"
            cond_totals = _rebuild_totals_from_bucket(stats)
            if cond_totals:
                sold_stats_store.append_snapshot(isbn, 90, cond_key, cond_totals)
    except Exception:
        logger.debug("sold_stats_store append failed isbn=%s (non-fatal)", isbn)

    return result


def _rebuild_totals_from_stats(result: Dict[str, Any]) -> list:
    """finding_sold_stats sonucundan approximated price list üret."""
    avg = result.get("sold_avg")
    count = result.get("sold_count") or 0
    if avg is None or count == 0:
        return []
    # Ortalamayı count kez tekrar et (yaklaşım — gerçek dağılım bilinmiyor)
    return [float(avg)] * min(count, 50)


def _rebuild_totals_from_bucket(stats: Dict[str, Any]) -> list:
    """by_condition bucket'ından approximated price list üret."""
    avg = stats.get("avg")
    count = stats.get("count") or 0
    if avg is None or count == 0:
        return []
    return [float(avg)] * min(count, 30)


async def _check_isbn(client: httpx.AsyncClient, isbn: str) -> int:
    sent = 0
    try:
        items = await _fetch(client, isbn)
    except Exception:
        logger.exception("ISBN %s fetch failed", isbn)
        return 0

    candidates = _pick_candidates_under_limit(items, isbn)
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

        msg = _format_message(isbn, it, bucket, total, limit, sold_avg=avg_for_msg, sold_count=count_for_msg, ship_estimated=it.get("_shipping_estimated", False))

        # Decide label for history
        make_offer = "BEST_OFFER" in (it.get("buyingOptions") or [])
        decision = "OFFER" if make_offer else "BUY"

        # Extract image URL from eBay Browse response
        image_url = ""
        img = it.get("image") or {}
        if isinstance(img, dict):
            image_url = img.get("imageUrl") or ""
        if not image_url:
            thumbs = it.get("thumbnailImages") or []
            if thumbs and isinstance(thumbs[0], dict):
                image_url = thumbs[0].get("imageUrl") or ""

        ok = await _send_telegram(msg)
        if ok:
            sent += 1
            # Write to alert history (fire-and-forget; don't block on error)
            try:
                alert_history_store.add_entry(
                    isbn=isbn,
                    item_id=item_id,
                    title=(it.get("title") or "")[:120],
                    condition=bucket,
                    total=total,
                    limit=limit,
                    decision=decision,
                    url=it.get("itemWebUrl") or "",
                    image_url=image_url,
                    sold_avg=avg_for_msg,
                    sold_count=count_for_msg,
                    ship_estimated=it.get("_shipping_estimated", False),
                )
                logger.info("HISTORY_WRITE isbn=%s item=%s decision=%s total=%.2f", isbn, item_id, decision, total)
            except Exception as _he:
                logger.warning("alert_history write failed: %s", _he)

    return sent


def _interval_for_isbn(isbn: str) -> int:
    """Per-ISBN interval from rules_store, falling back to global sched_tick_seconds."""
    r = get_rule(isbn)
    sec = r.interval_seconds
    if isinstance(sec, int) and sec > 0:
        return sec
    return int(get_settings().sched_tick_seconds)


async def run_once(force_all: bool = False) -> None:
    """
    Scan due ISBNs once.
    force_all=True → tüm ISBN'ler due sayılır (interval kontrolü atlanır).
    Bu, servis başlangıcında ilk taramanın anında yapılması için kullanılır.
    """
    s = get_settings()
    isbns = isbn_store.list_isbns()
    if not isbns:
        logger.info("Watchlist empty")
        return

    now = time.time()
    due_isbns: List[str] = []
    for isbn in isbns:
        if force_all:
            due_isbns.append(isbn)
        else:
            interval = _interval_for_isbn(isbn)
            if run_state.due(isbn, interval, now=now):
                due_isbns.append(isbn)

    if force_all:
        logger.info("Startup scan: forcing %d/%d ISBN (force_all=True)", len(due_isbns), len(isbns))
    else:
        logger.info("Checking %d/%d ISBN (due)", len(due_isbns), len(isbns))

    if not due_isbns:
        return

    async with httpx.AsyncClient(timeout=20) as client:
        for isbn in due_isbns:
            sent = await _check_isbn(client, isbn)
            run_state.set_last_run(isbn, ts=now)
            logger.info("isbn=%s alerts=%d", isbn, sent)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    s = get_settings()
    tick = int(s.sched_tick_seconds)
    logger.info("Scheduler start tick=%ss ebay_env=%s", tick, s.ebay_env)

    # İlk çalıştırmada tüm ISBN'leri tara (last_run ne olursa olsun)
    first_run = True
    while True:
        try:
            await run_once(force_all=first_run)
            first_run = False
        except Exception:
            logger.exception("run_once crash")
            first_run = False
        await asyncio.sleep(tick)


if __name__ == "__main__":
    asyncio.run(main())
