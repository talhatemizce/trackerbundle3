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
from app.ebay_client import browse_search_isbn, finding_sold_stats, normalize_condition, item_total_price, hybrid_verify_items
from app.alert_store import check_and_mark
from app import alert_history_store
from app import sold_stats_store

logger = logging.getLogger("trackerbundle.scheduler_ebay")


# ── Deal Score ────────────────────────────────────────────────────────────────
# 0-100 arası tek sayı. Yüksek = daha iyi fırsat.
# Formül:
#   Baz puan:   (1 - total/base_limit) * 70   → limit'e ne kadar yakın (0-70)
#   Make Offer: +10 (pazarlık var)
#   Condition:  brand_new/like_new +8, very_good +5, good 0, acceptable -5
#   Ship est.:  -8 (gerçek maliyet belirsiz)
#   Sold below: avg_for_msg < total → -5 (aktif fiyat piyasanın üstünde)
# Sonuç [0, 100] arasına kısılır.

_COND_BONUS = {
    "brand_new": 8, "like_new": 8, "very_good": 5,
    "good": 0, "acceptable": -5, "used_all": 0,
}

def deal_score(
    total: float,
    base_limit: float,        # make_offer multiplier UYGULANMAMIŞ limit
    bucket: str,
    make_offer: bool = False,
    ship_estimated: bool = False,
    sold_avg: float | None = None,
) -> int:
    if base_limit <= 0:
        return 0
    ratio_score = max(0.0, (1.0 - total / base_limit)) * 70.0
    bonus = 0
    bonus += 10 if make_offer else 0
    bonus += _COND_BONUS.get(bucket, 0)
    bonus += -8 if ship_estimated else 0
    bonus += -5 if (sold_avg is not None and sold_avg < total) else 0
    return max(0, min(100, int(round(ratio_score + bonus))))


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
    match_quality: str = "CONFIRMED",
    score: int | None = None,
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
    verify_badge = "" if match_quality == "CONFIRMED" else " ⚠️ unverified"
    ship_note = " · est. ship" if ship_estimated else ""

    # Score badge
    if score is not None:
        if score >= 75:
            score_str = f" 🔥{score}"
        elif score >= 50:
            score_str = f" ✨{score}"
        else:
            score_str = f" [{score}]"
    else:
        score_str = ""

    msg = (
        f"📚 <b>{title}</b>\n"
        f"ISBN: {isbn} | {label}{offer_str}{verify_badge}\n"
        f"Total: <b>${total_i}</b>{ship_note} (limit ${limit_i}) → {decision_str}{score_str}\n"
    )

    # Sold stats satırı
    if sold_avg is not None:
        sold_str = f"Sold avg: ${sold_avg}"
        if sold_count is not None:
            sold_str += f" ({sold_count} sold)"
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
    """
    Hybrid verification pipeline (N=15):
      1. Browse search (GTIN-first, category fallback)
      2. Pre-filter by price limit → top N=15 cheapest candidates
      3. hybrid_verify_items → CONFIRMED / UNVERIFIED_SUPER_DEAL / DROP
      4. Dedup check → send Telegram + write history
    """
    sent = 0
    try:
        items = await _fetch(client, isbn)
    except Exception:
        logger.exception("ISBN %s fetch failed", isbn)
        return 0

    # ── Adım 1: price/limit pre-filter (limit altındaki tüm items, max 15) ──
    s = get_settings()
    calc_est = s.calculated_ship_estimate_usd if s.calculated_ship_estimate_usd > 0 else None

    pre: List[Tuple[Dict, str, float, float]] = []
    for it in items:
        total = item_total_price(it, calc_ship_est=calc_est)
        if total is None:
            continue
        bucket = normalize_condition(it.get("condition"), it.get("conditionId"))
        lim_info = effective_limit(isbn, bucket)
        lim = float(lim_info["limit"])
        if "BEST_OFFER" in (it.get("buyingOptions") or []):
            lim = float(round(lim * float(s.make_offer_multiplier), 2))
        if total <= lim:
            pre.append((it, bucket, total, lim))

    pre.sort(key=lambda x: x[2])
    pre = pre[:15]  # N=15

    logger.info("isbn=%s pre_candidates=%d (price ≤ limit, before verify)", isbn, len(pre))
    if not pre:
        return 0

    # ── Adım 2: hybrid verification ─────────────────────────────────────────
    limit_map  = {str(it.get("itemId") or ""): lim   for it, _, _, lim in pre}
    bucket_map = {str(it.get("itemId") or ""): bucket for it, bucket, _, _ in pre}
    pre_items  = [it for it, _, _, _ in pre]

    verified = await hybrid_verify_items(
        client, isbn, pre_items, limit_map=limit_map, bucket_map=bucket_map
    )
    logger.info("isbn=%s verified_candidates=%d (after hybrid verify)", isbn, len(verified))

    if not verified:
        return 0

    # ── Adım 3: sold stats (non-blocking) ───────────────────────────────────
    sold = await _fetch_sold(client, isbn)
    sold_overall_avg   = sold.get("sold_avg")
    sold_overall_count = sold.get("sold_count")
    sold_by_cond       = sold.get("by_condition", {})

    # ── Adım 4: dedup + Telegram + history ──────────────────────────────────
    for it in verified:
        item_id = str(it.get("itemId") or "")
        if not item_id:
            continue

        already = check_and_mark(isbn, item_id)
        if already:
            logger.info("isbn=%s item=%s skip=already_notified", isbn, item_id)
            continue

        bucket = bucket_map.get(item_id, "used_all")
        total  = float(item_total_price(it, calc_ship_est=calc_est) or 0)
        limit  = limit_map.get(item_id, 0.0)

        match_quality = it.get("_match_quality", "CONFIRMED")
        verified_flag = it.get("_verified", True)
        verify_reason = it.get("_verification_reason", "gtins_match")

        cond_stats    = sold_by_cond.get(bucket, {})
        avg_for_msg   = cond_stats.get("avg") if cond_stats.get("avg") is not None else sold_overall_avg
        count_for_msg = cond_stats.get("count") if cond_stats.get("count") is not None else sold_overall_count

        make_offer = "BEST_OFFER" in (it.get("buyingOptions") or [])
        decision   = "OFFER" if make_offer else "BUY"
        ship_est   = it.get("_shipping_estimated", False)

        # base_limit = make_offer multiplier uygulanmadan önceki limit
        s_cfg = get_settings()
        base_limit = limit / float(s_cfg.make_offer_multiplier) if make_offer else limit

        score = deal_score(
            total=total,
            base_limit=base_limit,
            bucket=bucket,
            make_offer=make_offer,
            ship_estimated=ship_est,
            sold_avg=float(avg_for_msg) if avg_for_msg is not None else None,
        )

        # Image URL
        image_url = ""
        img = it.get("image") or {}
        if isinstance(img, dict):
            image_url = img.get("imageUrl") or ""
        if not image_url:
            thumbs = it.get("thumbnailImages") or []
            if thumbs and isinstance(thumbs[0], dict):
                image_url = thumbs[0].get("imageUrl") or ""

        msg = _format_message(
            isbn, it, bucket, total, limit,
            sold_avg=avg_for_msg, sold_count=count_for_msg,
            ship_estimated=ship_est,
            match_quality=match_quality,
            score=score,
        )

        ok = await _send_telegram(msg)
        if ok:
            sent += 1
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
                    ship_estimated=ship_est,
                    match_quality=match_quality,
                    verified=verified_flag,
                    verification_reason=verify_reason,
                    deal_score=score,
                )
                logger.info(
                    "HISTORY_WRITE isbn=%s item=%s decision=%s total=%.2f score=%d quality=%s",
                    isbn, item_id, decision, total, score, match_quality,
                )
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
