"""
AI Analyst v2.1 — Hibrit kitap arbitraj analizi.

Mimari:
- Deterministic (kural/API): edition, satıcı sayısı, kondisyon, mevsimsellik → %70 karar
- AI (Gemini Vision + Google Search): kapak doğrulama, gri alan yorumlama → %30

Amazon buybox'ta → verdict DEĞİŞTİRME, sadece confidence kırp + risk flag.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Any, Dict, List, Optional

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

GEMINI_API_BASE      = "https://generativelanguage.googleapis.com/v1beta/models"
MODEL                = "gemini-2.5-flash-lite"
GOOGLE_BOOKS_API     = "https://www.googleapis.com/books/v1/volumes"
OPEN_LIBRARY_API     = "https://openlibrary.org/api/books"
OPEN_LIBRARY_SEARCH  = "https://openlibrary.org/search.json"
OPEN_LIBRARY_WORKS   = "https://openlibrary.org"   # + /works/{key}/editions.json
OL_USER_AGENT        = "TrackerBundle3/1.0 (book arbitrage tool; contact via github)"


# ─── Kondisyon uyumsuzluğu (deterministic, skor tabanlı) ──────────────────────

_DAMAGE_KEYWORDS = [
    ("writing", 3), ("written in", 3), ("highlighted", 3), ("underlining", 3),
    ("underlined", 3), ("notes", 2), ("annotations", 3), ("stain", 3),
    ("water damage", 4), ("water stain", 4), ("mold", 4), ("mildew", 4),
    ("smell", 2), ("odor", 2), ("torn", 3), ("missing pages", 4),
    ("ex-library", 3), ("ex library", 3), ("library copy", 3),
    ("teacher's edition", 4), ("teachers edition", 4), ("instructor", 3),
    ("worn", 2), ("heavy wear", 3), ("damaged", 3), ("loose", 2),
    ("binding issues", 3), ("spine damage", 3), ("cover damage", 3),
]

def _condition_score(title: str, description: str, declared_condition: str) -> Dict[str, Any]:
    """
    0-10 arası mismatch skoru. 0=temiz, 10=çok kötü.
    Her keyword ağırlıklı puan ekler, 10'da cap.
    """
    combined = (title + " " + description).lower()
    flags: List[str] = []
    score = 0

    for kw, weight in _DAMAGE_KEYWORDS:
        if kw in combined:
            flags.append(kw)
            score += weight

    # Kondisyon çelişkisi
    if declared_condition == "new":
        if any(w in combined for w in ["pre-owned", "used", "second hand"]):
            flags.append("listed as NEW but description says used/pre-owned")
            score += 4

    score = min(score, 10)
    risk = "HIGH" if score >= 6 else "MEDIUM" if score >= 3 else "LOW"
    return {"condition_flags": flags, "condition_risk": risk, "condition_score": score}


# ─── Edition check (Google Books + Open Library yedek) ────────────────────────

async def _check_edition(isbn: str, client: httpx.AsyncClient) -> Dict[str, Any]:
    """
    ISBN için kitap metadata + yeni baskı tespiti.

    Akış:
      1. Google Books  → title, authors, publisher, categories, description, pageCount
      2. Open Library Search → subjects, work_key (editions endpoint için)
      3. Work & Edition API  → tüm baskıların yıllarını karşılaştır (en güvenilir yöntem)
         Fallback: Google Books secondary search (eski yöntem)
      4. OL Books API (son çare, jscmd=data)
    """
    isbn13 = _to_isbn13(isbn) or isbn
    headers = {"User-Agent": OL_USER_AGENT}

    # ── 1. Google Books — birincil metadata kaynağı ───────────────────────────
    gb_meta: Dict[str, Any] = {}
    try:
        r = await client.get(
            GOOGLE_BOOKS_API,
            params={"q": f"isbn:{isbn13}", "maxResults": 1,
                    "fields": "items(volumeInfo(title,authors,publishedDate,publisher,categories,description,pageCount,averageRating,ratingsCount))"},
            timeout=10,
        )
        if r.status_code == 200:
            items = r.json().get("items") or []
            if items:
                vi = items[0].get("volumeInfo", {})
                pub_date = vi.get("publishedDate", "")
                year = int(pub_date[:4]) if pub_date and len(pub_date) >= 4 else None
                gb_meta = {
                    "edition_year": year,
                    "google_title":  vi.get("title", ""),
                    "authors":       vi.get("authors") or [],
                    "publisher":     vi.get("publisher", ""),
                    "categories":    vi.get("categories") or [],
                    "description":   (vi.get("description") or "")[:300],
                    "page_count":    vi.get("pageCount"),
                    "avg_rating":    vi.get("averageRating"),
                    "ratings_count": vi.get("ratingsCount"),
                    "source":        "google_books",
                }
    except Exception as e:
        logger.debug("Google Books error: %s", e)

    # ── 2. Open Library Search — subjects + work_key ──────────────────────────
    ol_meta: Dict[str, Any] = {}
    work_key: Optional[str] = None
    try:
        r = await client.get(
            OPEN_LIBRARY_SEARCH,
            params={"isbn": isbn13, "fields": "key,title,author_name,publisher,subject,first_publish_year,number_of_pages_median"},
            headers=headers, timeout=10,
        )
        if r.status_code == 200:
            docs = r.json().get("docs") or []
            if docs:
                doc = docs[0]
                work_key = doc.get("key")  # e.g. "/works/OL45804W"
                year = doc.get("first_publish_year")
                try: year = int(year) if year else None
                except (ValueError, TypeError): year = None
                subjects   = [s for s in (doc.get("subject")      or []) if isinstance(s, str)][:6]
                publishers = doc.get("publisher") or []
                authors    = doc.get("author_name") or []
                ol_meta = {
                    "edition_year": year,
                    "google_title": doc.get("title", ""),
                    "authors":      authors,
                    "publisher":    publishers[0] if publishers else "",
                    "categories":   subjects,
                    "description":  "",
                    "page_count":   doc.get("number_of_pages_median"),
                    "source":       "open_library",
                }
    except Exception as e:
        logger.debug("Open Library Search error: %s", e)

    # ── 3. Work & Edition API — has_newer_edition (en güvenilir yöntem) ───────
    has_newer: Optional[bool] = None
    current_year = gb_meta.get("edition_year") or ol_meta.get("edition_year")

    if work_key and current_year:
        try:
            r = await client.get(
                f"{OPEN_LIBRARY_WORKS}{work_key}/editions.json",
                params={"limit": 100, "fields": "isbn_13,isbn_10,publish_date"},
                headers=headers, timeout=12,
            )
            if r.status_code == 200:
                entries = r.json().get("entries") or []
                edition_years: List[int] = []
                current_isbn_year: Optional[int] = None
                for e in entries:
                    isbns13 = e.get("isbn_13") or []
                    isbns10 = e.get("isbn_10") or []
                    pub_date = e.get("publish_date", "")
                    yr = None
                    for chunk in (pub_date or "").split():
                        try:
                            y = int(chunk)
                            if 1900 < y < 2030:
                                yr = y; break
                        except (ValueError, TypeError):
                            pass
                    if yr:
                        edition_years.append(yr)
                        # Bu baskı bizim ISBN'imiz mi?
                        all_isbns = isbns13 + isbns10
                        if isbn13 in all_isbns or isbn in all_isbns:
                            current_isbn_year = yr
                if edition_years:
                    newest_year = max(edition_years)
                    check_year  = current_isbn_year or current_year
                    has_newer   = newest_year > check_year
                    logger.debug(
                        "Work editions isbn=%s work=%s editions=%d newest=%d current=%d has_newer=%s",
                        isbn13, work_key, len(edition_years), newest_year, check_year, has_newer,
                    )
        except Exception as e:
            logger.debug("Work & Edition API error isbn=%s: %s", isbn13, e)

    # Fallback: Google Books secondary search (has_newer tespit edilemezse)
    if has_newer is None and gb_meta.get("google_title") and current_year:
        try:
            title   = gb_meta["google_title"]
            authors = gb_meta.get("authors") or []
            query   = f'intitle:"{title[:25]}"'
            if authors:
                query += f' inauthor:"{authors[0].split()[-1]}"'
            r2 = await client.get(
                GOOGLE_BOOKS_API,
                params={"q": query, "maxResults": 8, "orderBy": "newest",
                        "fields": "items(volumeInfo(publishedDate,industryIdentifiers))"},
                timeout=10,
            )
            if r2.status_code == 200:
                for item in (r2.json().get("items") or []):
                    pd     = (item.get("volumeInfo") or {}).get("publishedDate", "")
                    idents = (item.get("volumeInfo") or {}).get("industryIdentifiers") or []
                    item_isbns = [x.get("identifier", "") for x in idents]
                    if isbn13 in item_isbns or isbn in item_isbns:
                        continue  # aynı baskı
                    if pd and len(pd) >= 4:
                        try:
                            if int(pd[:4]) > current_year:
                                has_newer = True
                                break
                        except (ValueError, TypeError):
                            pass
        except Exception as e:
            logger.debug("Google Books newer-edition fallback error: %s", e)

    # ── Sonuçları birleştir: Google Books metadata öncelikli, OL subjects ekle ─
    base = gb_meta if gb_meta else ol_meta
    if gb_meta and ol_meta:
        # OL'un subjects'i genellikle daha zengin — merge
        merged_cats = list(dict.fromkeys(
            (gb_meta.get("categories") or []) + (ol_meta.get("categories") or [])
        ))[:8]
        base = {**ol_meta, **gb_meta, "categories": merged_cats}
        # OL page_count bazen daha doğru (median of all editions)
        if not gb_meta.get("page_count") and ol_meta.get("page_count"):
            base["page_count"] = ol_meta["page_count"]

    if not base:
        # Son çare: OL Books API (jscmd=data)
        try:
            r = await client.get(
                OPEN_LIBRARY_API,
                params={"bibkeys": f"ISBN:{isbn13}", "format": "json", "jscmd": "data"},
                headers=headers, timeout=10,
            )
            if r.status_code == 200:
                data = r.json()
                book = data.get(f"ISBN:{isbn13}") or {}
                if book:
                    pub_date = book.get("publish_date", "")
                    year = None
                    for chunk in pub_date.split():
                        try:
                            y = int(chunk)
                            if 1900 < y < 2030:
                                year = y; break
                        except (ValueError, TypeError): pass
                    publishers = [p.get("name","") for p in (book.get("publishers") or []) if isinstance(p, dict)]
                    subjects   = [s.get("name","") for s in (book.get("subjects")   or []) if isinstance(s, dict)]
                    authors    = [a.get("name","") for a in (book.get("authors")    or []) if isinstance(a, dict)]
                    ol_desc    = book.get("description", "")
                    if isinstance(ol_desc, dict): ol_desc = ol_desc.get("value", "")
                    base = {
                        "edition_year": year,
                        "google_title": book.get("title", ""),
                        "authors":      authors,
                        "publisher":    publishers[0] if publishers else "",
                        "categories":   subjects[:5],
                        "description":  (ol_desc or "")[:300],
                        "page_count":   book.get("number_of_pages"),
                        "source":       "open_library_books",
                    }
        except Exception as e:
            logger.debug("Open Library Books API error: %s", e)

    result = dict(base)
    result["has_newer_edition"] = has_newer
    result["work_key"]          = work_key  # downstream kullanım için

    # ── 5. HathiTrust + Library of Congress — DDC / LC classification ─────────
    # Bu iki ücretsiz kaynak "textbook mı yoksa trade kitap mı?" sorusunu
    # doğrudan yanıtlıyor: LC call number ve Dewey Decimal ile.
    result["dewey"]    = None
    result["lc_class"] = None
    result["is_textbook_likely"] = False  # default

    from app.core.config import get_settings as _cfg_gs
    _s = _cfg_gs()
    if getattr(_s, "hathitrust_enabled", True):
        # 5a. HathiTrust brief — DDC (082 MARC field)
        try:
            _ht_url = f"https://catalog.hathitrust.org/api/volumes/brief/isbn/{isbn13}.json"
            _r = await client.get(
                _ht_url,
                headers={"User-Agent": OL_USER_AGENT},
                timeout=8,
            )
            if _r.status_code == 200:
                _ht = _r.json()
                # Cevap: {records: {"/books/OL...": {data: {dewey_decimal_class: ["512.5"]}}}}
                for _rec in (_ht.get("records") or {}).values():
                    _data = _rec.get("data") or {}
                    _dewey_list = _data.get("dewey_decimal_class") or []
                    if _dewey_list:
                        result["dewey"] = str(_dewey_list[0]).strip()
                        break
                    # MARC 082 sometimes in different structure
                    for _item in (_ht.get("items") or []):
                        _marc_dd = (_item.get("marc") or {}).get("082") or []
                        if _marc_dd:
                            result["dewey"] = str(_marc_dd[0]).strip()
                            break
                    if result["dewey"]:
                        break
        except Exception as _ht_err:
            logger.debug("HathiTrust error isbn=%s: %s", isbn13, _ht_err)

        # 5b. Library of Congress — LC call number + subjects (no API key, 20 req/min)
        if not result["lc_class"]:
            try:
                _loc_url = "https://www.loc.gov/search/"
                _r2 = await client.get(
                    _loc_url,
                    params={"q": isbn13, "fo": "json", "c": 1, "at": "results"},
                    headers={"User-Agent": OL_USER_AGENT},
                    timeout=8,
                )
                if _r2.status_code == 200:
                    _loc_results = (_r2.json().get("results") or [])
                    if _loc_results:
                        _loc_item = _loc_results[0]
                        # LC call number
                        _call_numbers = _loc_item.get("call_number") or []
                        if _call_numbers:
                            result["lc_class"] = str(_call_numbers[0]).strip()
                        # Dewey fallback from LoC if HathiTrust missed
                        if not result["dewey"]:
                            _subjects_loc = _loc_item.get("subject") or []
                            if _subjects_loc and not result.get("categories"):
                                result["categories"] = [str(s) for s in _subjects_loc[:6]]
            except Exception as _loc_err:
                logger.debug("LoC API error isbn=%s: %s", isbn13, _loc_err)

    # 5c. Derive textbook classification from DDC / LC / subjects
    try:
        from app.analytics import dewey_to_category, lc_class_to_category, subjects_to_textbook_score
        _tb_score = 0.0
        _category_meta = {}
        if result.get("dewey"):
            _category_meta = dewey_to_category(result["dewey"])
            _tb_score = max(_tb_score, 0.8 if _category_meta.get("is_textbook_likely") else 0.1)
            if not result.get("categories") and _category_meta.get("category"):
                result.setdefault("categories", [])
                if _category_meta["category"] not in result["categories"]:
                    result["categories"].insert(0, _category_meta["category"])
        if result.get("lc_class"):
            _lc_meta = lc_class_to_category(result["lc_class"])
            _tb_score = max(_tb_score, 0.75 if _lc_meta.get("is_textbook_likely") else 0.1)
        if result.get("categories"):
            _subj_score = subjects_to_textbook_score(result["categories"])
            _tb_score = max(_tb_score, _subj_score)
        result["is_textbook_likely"] = _tb_score >= 0.5
        result["textbook_score"]     = round(_tb_score, 2)
    except Exception as _tb_err:
        logger.debug("Textbook classification error: %s", _tb_err)

    return result


# ─── eBay kapak resmi ──────────────────────────────────────────────────────────

async def _fetch_image_b64(url: str, client: httpx.AsyncClient) -> Optional[str]:
    if not url:
        return None
    try:
        r = await client.get(url, timeout=15, follow_redirects=True)
        if r.status_code == 200 and "image" in r.headers.get("content-type", ""):
            return base64.standard_b64encode(r.content).decode()
    except Exception as e:
        logger.debug("Image fetch error: %s", e)
    return None


# ─── Confidence ayarlama (deterministic override) ─────────────────────────────

def _apply_deterministic_adjustments(result: Dict[str, Any], candidate: Dict[str, Any],
                                      edition: Dict[str, Any], cond: Dict[str, Any]) -> Dict[str, Any]:
    """
    AI kararını deterministic verilerle ezip değiştirmeden, confidence ve risk'i ayarla.
    Verdict asla burada değiştirilmez — bu AI'nın işi.
    """
    risks = list(result.get("risks") or [])
    confidence = result.get("confidence", 50)

    # Amazon kendisi buybox'ta → confidence %20 kırp
    if candidate.get("amazon_is_sold_by_amazon"):
        confidence = max(10, int(confidence * 0.88))
        risks.insert(0, "🚫 Amazon kendisi buybox'ta — rekabet çok zorlaşır, kazanma oranı düşük")

    # Çok fazla satıcı → confidence %10 kırp
    seller_count = candidate.get("amazon_seller_count") or 0
    if seller_count > 20:
        confidence = max(5, int(confidence * 0.90))
        risks.append(f"👥 {seller_count} satıcı var — yoğun rekabet")
    elif seller_count > 10:
        risks.append(f"👥 {seller_count} satıcı — orta rekabet")

    # Yeni baskı tespit edildi → confidence %15 kırp
    if edition.get("has_newer_edition"):
        confidence = max(5, int(confidence * 0.85))
        yr = edition.get("edition_year")
        risks.insert(0, f"⚠️ Daha yeni baskı var{(' (bu baskı ' + str(yr) + ')') if yr else ''} — talep azalıyor olabilir")

    # Kondisyon riski → confidence kırp
    cond_score = cond.get("condition_score", 0)
    if cond_score >= 6:
        confidence = max(5, int(confidence * 0.75))
        risks.append(f"🚩 Kondisyon riski YÜKSEK (skor: {cond_score}/10) — ilanda hasar belirtileri var")
    elif cond_score >= 3:
        confidence = max(10, int(confidence * 0.90))
        risks.append(f"🚩 Kondisyon riski ORTA (skor: {cond_score}/10)")

    # Mevsimsellik
    sm = candidate.get("seasonality_mult")
    if sm and sm < 0.8:
        risks.append(f"📅 Düşük sezon ({sm}x) — satış yavaş olabilir")
    elif sm and sm >= 1.2:
        result["summary"] = (result.get("summary") or "") + f" Bu ay yüksek sezon ({sm}x)."

    result["confidence"] = min(100, max(0, confidence))
    result["risks"] = risks

    # Risk level deterministic override (condition çok kötüyse)
    if cond_score >= 6 and result.get("risk_level") == "LOW":
        result["risk_level"] = "MEDIUM"
    if edition.get("has_newer_edition") and result.get("risk_level") == "LOW":
        result["risk_level"] = "MEDIUM"

    # ── VERDICT OVERRIDE: Sayılar konuştuğunda Gemini'yi dinleme ────────
    # Gemini non-deterministic (farklı web araması → farklı karar).
    # Rakamlar net olduğunda rule-based verdict Gemini'yi override eder.
    result = _apply_verdict_override(result, candidate, cond_score)

    return result


def _apply_verdict_override(result: dict, candidate: dict, cond_score: int) -> dict:
    """
    Rakamlar net olduğunda Gemini verdict'ini override eder.

    BUY override koşulları (hepsi AND):
      - ROI >= 30% (fire tier)
      - Profit >= $5
      - Risk level != HIGH
      - Condition score < 6 (ağır hasar yok)
      - ISBN conflict yok

    PASS override koşulları (herhangi biri OR):
      - Profit <= 0
      - ROI < 0
      - Risk level == HIGH AND cond_score >= 6
    """
    roi = candidate.get("roi_pct", 0) or 0
    profit = candidate.get("profit", 0) or 0
    risk = result.get("risk_level", "UNKNOWN")
    isbn_conflict = result.get("isbn_conflict", False)
    gemini_verdict = result.get("verdict", "UNKNOWN")

    override_reason = None

    # ── PASS override: rakamlar kötü ──
    if profit <= 0 or roi < 0:
        if gemini_verdict not in ("PASS",):
            result["verdict"] = "PASS"
            override_reason = f"Negatif kâr (${profit})"

    # ── BUY override: rakamlar çok iyi ──
    # İki seviye:
    #   Tier 1 (EXTREME): ROI >= %100 + profit >= $20 → HIGH risk bile override et
    #           (Gemini'nin "veri bulamadım" HIGH'ı gerçek risk değil)
    #           Sadece isbn_conflict ve cond_score >= 6 engeller.
    #   Tier 2 (STRONG):  ROI >= %30 + profit >= $5 → HIGH risk hariç override et
    elif (roi >= 100 and profit >= 20
          and cond_score < 6
          and not isbn_conflict):
        if gemini_verdict != "BUY":
            result["verdict"] = "BUY"
            override_reason = f"ROI {roi}%, kâr ${profit} — rakamlar tartışmasız"
            # Gemini'nin data-uncertainty HIGH'ını MEDIUM'a düşür
            if risk == "HIGH":
                result["risk_level"] = "MEDIUM"

    elif (roi >= 30 and profit >= 5
          and risk != "HIGH"
          and cond_score < 6
          and not isbn_conflict):
        if gemini_verdict != "BUY":
            result["verdict"] = "BUY"
            override_reason = f"ROI {roi}%, kâr ${profit} — sayılar net"

    # ── WATCH override: orta bölge, Gemini PASS demiş ama rakamlar fena değil ──
    elif (roi >= 15 and profit >= 3
          and risk != "HIGH"
          and cond_score < 6
          and not isbn_conflict
          and gemini_verdict == "PASS"):
        result["verdict"] = "WATCH"
        override_reason = f"ROI {roi}% makul ama Gemini tereddütlü"

    if override_reason:
        result["verdict_override"] = True
        result["verdict_override_reason"] = override_reason
        logger.info("Verdict override: %s → %s (%s) isbn=%s",
                     gemini_verdict, result["verdict"], override_reason,
                     candidate.get("isbn", "?"))

        # ── Override yapınca güven, risk ve öneriyi de hizala ──────────
        # Gemini %40 güven + "SKIP" deyip BUY override etmek çelişkili.
        # Sayısal kanıt güçlüyse tüm sinyaller tutarlı olmalı.
        if result["verdict"] == "BUY":
            # Confidence: sayısal kesinliğe göre hesapla
            # ROI ne kadar yüksekse güven o kadar yüksek
            numeric_conf = min(95, 60 + int(roi / 10))  # ROI%30→63, %100→70, %287→88
            result["confidence"] = max(result.get("confidence", 0), numeric_conf)
            # Risk: rakamlar iyi + condition temiz → LOW
            if risk in ("MEDIUM", "UNKNOWN") and cond_score < 3:
                result["risk_level"] = "LOW"
            # Recommendation: Gemini'nin "SKIP" önerisi override
            buy_price = candidate.get("buy_price", 0)
            result["recommendation"] = (
                f"Max ${round(buy_price * 1.15, 2)} — mevcut fiyat (${buy_price}) "
                f"ROI %{roi} ve kâr ${profit} ile güçlü fırsat."
            )
        elif result["verdict"] == "PASS":
            result["confidence"] = max(result.get("confidence", 0), 80)
            result["recommendation"] = f"Kâr negatif (${profit}) — bu fiyattan almayın."

    return result


# ─── Ana analiz ────────────────────────────────────────────────────────────────

# ── AI Result Cache (Gemini kota koruması) ────────────────────────────────────
# Aynı ISBN için tekrar Gemini çağırmaz — günlük kota 10-15 sorgu ile dolar.
import time as _time
_ai_cache: Dict[str, Dict[str, Any]] = {}
_ai_cache_lock = asyncio.Lock()
_AI_CACHE_TTL = 3600 * 6  # 6 saat
# In-flight set: aynı ISBN için paralel duplicate çağrı önle
_ai_inflight: set = set()


async def analyze_isbn(isbn: str, candidate: Dict[str, Any]) -> Dict[str, Any]:
    s = get_settings()
    # Router birden fazla provider dener — en az birinin key'i olmalı
    from app.llm_router import get_status as _llm_status
    _status = _llm_status()
    _configured = [name for name, st in _status.items() if st.get("configured")]
    if not _configured:
        raise RuntimeError("Hiçbir LLM API key'i yapılandırılmamış — GEMINI_API_KEY, GROQ_API_KEY veya OPENROUTER_API_KEY gerekli")

    # ── Cache kontrolü ────────────────────────────────────────
    # Composite key: ISBN + buy_price_bucket (her $5 band) + source_condition
    # Böylece aynı ISBN'in farklı fiyat/kondisyon kombinasyonları ayrı analiz alır
    isbn_clean = isbn.replace("-", "").replace(" ", "").strip()
    buy_price = float(candidate.get("buy_price") or candidate.get("ebay_total") or 0)
    price_bucket = int(buy_price // 5) * 5  # $5 granülariyle grupla (28.94 → 25)
    source_cond = candidate.get("source_condition", "used")
    # Seller-level granularity: same ISBN + same price band + same seller = reuse
    # Different sellers can have different conditions/descriptions/images
    item_id = candidate.get("item_id") or candidate.get("ebay_item_id") or ""
    seller = candidate.get("ebay_seller_name") or candidate.get("seller_name") or ""
    # Use item_id if available (most specific), else seller name
    listing_key = item_id[:16] if item_id else seller[:20]
    cache_key = f"{isbn_clean}:{price_bucket}:{source_cond}:{listing_key}"

    # Step 1: cache read (lock held briefly, no await inside)
    async with _ai_cache_lock:
        if cache_key in _ai_cache:
            cached = _ai_cache[cache_key]
            if _time.time() - cached.get("_cached_at", 0) < _AI_CACHE_TTL:
                logger.info("AI cache HIT key=%s", cache_key)
                return {**cached, "_from_cache": True}
        already_inflight = cache_key in _ai_inflight
        if not already_inflight:
            _ai_inflight.add(cache_key)

    # Step 2: if another coroutine is already computing this key, wait OUTSIDE the lock
    if already_inflight:
        logger.info("AI in-flight, waiting for key=%s", cache_key)
        for _ in range(200):  # max 60s wait (200 × 0.3s)
            await asyncio.sleep(0.3)
            async with _ai_cache_lock:
                if cache_key in _ai_cache:
                    cached = _ai_cache[cache_key]
                    if _time.time() - cached.get("_cached_at", 0) < _AI_CACHE_TTL:
                        return {**cached, "_from_cache": True}
                if cache_key not in _ai_inflight:
                    # inflight cleared (completed or errored) — proceed with own call
                    _ai_inflight.add(cache_key)
                    break
        else:
            logger.warning("AI in-flight wait timed out for key=%s — proceeding", cache_key)
            async with _ai_cache_lock:
                _ai_inflight.add(cache_key)

    isbn13 = _to_isbn13(isbn) or isbn

    async with httpx.AsyncClient(timeout=30) as client:
        edition_task = _check_edition(isbn, client)
        img_url = candidate.get("ebay_image_url", "")
        image_task = _fetch_image_b64(img_url, client) if img_url else asyncio.sleep(0, result=None)
        edition_data, image_b64 = await asyncio.gather(edition_task, image_task)

    # Deterministic: kondisyon skoru
    cond_analysis = _condition_score(
        candidate.get("ebay_title", ""),
        candidate.get("ebay_description", ""),
        candidate.get("source_condition", "used"),
    )

    # AI: Gemini Vision + Google Search
    prompt = _build_prompt(isbn, isbn13, candidate, edition_data, cond_analysis)
    try:
        gemini_result = await _call_llm(prompt, image_b64)
    except Exception:
        async with _ai_cache_lock:
            _ai_inflight.discard(cache_key)
        raise

    # Deterministic ayarlamalar (verdict değiştirmeden confidence/risk)
    gemini_result = _apply_deterministic_adjustments(gemini_result, candidate, edition_data, cond_analysis)

    # Tüm verileri birleştir
    # ISBN conflict → auto HIGH risk
    if gemini_result.get("isbn_conflict"):
        if gemini_result.get("risk_level") not in ("HIGH",):
            gemini_result["risk_level"] = "HIGH"
        risks = gemini_result.get("risks") or []
        conflict_note = gemini_result.get("isbn_conflict_note", "")
        risks.insert(0, f"🚨 ISBN çakışması: {conflict_note}" if conflict_note else "🚨 Bu ISBN birden fazla farklı kitaba ait — doğrulanamadı")
        gemini_result["risks"] = risks

    gemini_result.update({
        "isbn": isbn,
        "edition_year": edition_data.get("edition_year"),
        "has_newer_edition": edition_data.get("has_newer_edition"),
        "google_title": edition_data.get("google_title", ""),
        "edition_source": edition_data.get("source", ""),
        "condition_flags": cond_analysis["condition_flags"],
        "condition_risk": cond_analysis["condition_risk"],
        "condition_score": cond_analysis["condition_score"],
        "image_verified": bool(image_b64),
        "amazon_seller_count": candidate.get("amazon_seller_count"),
        "amazon_is_sold_by_amazon": candidate.get("amazon_is_sold_by_amazon", False),
        "seasonality_mult": candidate.get("seasonality_mult"),
        "_cached_at": _time.time(),
    })

    # Cache'e kaydet ve inflight'tan çıkar
    async with _ai_cache_lock:
        _ai_cache[cache_key] = gemini_result
        _ai_inflight.discard(cache_key)
    return gemini_result


async def _call_llm(prompt: str, image_b64: Optional[str]) -> Dict[str, Any]:
    """
    Multi-LLM router ile analiz:
    - image_b64 varsa → vision task (Gemini)
    - image yoksa → reasoning task (Groq > Cerebras > OpenRouter > Gemini)
    429 / kota dolunca otomatik sonraki provider'a geçer.
    """
    from app.llm_router import route as llm_route

    sys_prompt = _system_prompt(bool(image_b64))
    task = "vision" if image_b64 else "reasoning"

    try:
        result = await llm_route(
            task=task,
            system_prompt=sys_prompt,
            user_prompt=prompt,
            image_b64=image_b64,
            max_tokens=1200,
            json_mode=(task == "reasoning"),  # enforce JSON for non-vision calls
        )
        text = result["text"]
        parsed = _parse_json(text)
        parsed["_provider"] = result.get("provider", "unknown")
        parsed["_model"] = result.get("model", "unknown")
        logger.info("AI analiz tamamlandı — provider=%s model=%s", result.get("provider"), result.get("model"))
        return parsed

    except Exception as e:
        logger.error("LLM router tamamen başarısız: %s", e)
        # _parse_json normalization ile tüm schema key'leri doldur — UI boş göstermez
        partial = {
            "verdict": "UNKNOWN",
            "summary": f"Tüm LLM providerlar başarısız: {str(e)[:120]}",
            "price_trend": "UNKNOWN",
            "price_trend_reason": "LLM erişilemiyor",
            "risk_level": "MEDIUM",
            "risks": ["Tüm LLM kotaları doldu veya yapılandırılmamış"],
            "confidence": 20,
            "all_providers_failed": True,
        }
        return _parse_json(str(partial).replace("'", '"'))


def _system_prompt(has_image: bool) -> str:
    img_part = """
Analyze the provided eBay listing image:
- Does the cover match this book (title/author visible)?
- Does condition look accurate?
- Add "image_verdict": "MATCH"|"MISMATCH"|"UNCERTAIN"
- Add "image_notes": brief observation
""" if has_image else '- Set "image_verdict": "NO_IMAGE", "image_notes": ""'

    return f"""You are a book arbitrage expert. Analyze the provided listing data and assess profitability.
ALL RELEVANT DATA IS PROVIDED BELOW — do NOT claim data is missing if it's in the prompt.

{img_part}

STEP 1: Verify the eBay listing matches the book (using title, ISBN, condition, image if provided).
STEP 2: Assess profit potential using the provided Amazon price, ROI, and seller data.
STEP 3: Evaluate risks (condition issues, competition, edition age).
STEP 4: Give your verdict based on the numbers provided.

IMPORTANT RULES:
- If ROI >= 30% and profit >= $5, this is likely a GOOD deal — say BUY unless there's a specific red flag.
- If ROI >= 100% and profit >= $20, this is an EXCELLENT deal — strong BUY.
- Do NOT say "insufficient data" when Amazon price, ROI, profit, and seller count are provided.
- Do NOT recommend "SKIP" for profitable deals unless there's a concrete risk (ISBN conflict, severe condition, Amazon is selling).
- Base buy_suggestion on the provided buy price with a small margin (10-15% above current).

Reply ONLY with this JSON (no markdown, no extra text):
{{
  "verdict": "BUY or PASS or WATCH",
  "confidence": 0-100,
  "summary": "2-3 sentence analysis.",
  "price_trend": "RISING or STABLE or DECLINING or UNKNOWN",
  "price_trend_reason": "brief explanation based on provided data",
  "risk_level": "LOW or MEDIUM or HIGH",
  "risks": [],
  "competitors": "comment on competing sellers",
  "buy_suggestion": "max price and preferred condition",
  "image_verdict": "MATCH or MISMATCH or UNCERTAIN or NO_IMAGE",
  "image_notes": "what you see in the image",
  "isbn_conflict": false,
  "isbn_conflict_note": "",
  "sources_checked": ["provided_data"]
}}"""


def _build_prompt(isbn: str, isbn13: str, c: Dict[str, Any],
                  edition: Dict[str, Any], cond: Dict[str, Any]) -> str:
    import datetime as dt

    worst = c.get("worst_case_profit")
    vel = c.get("velocity")
    vel_note = f"{vel}/mo" if vel else "None — NO BSR DATA, search web for real sales velocity"
    worst_note = (
        f"${worst} (⚠ model estimate only — BSR missing, NOT a real price floor)"
        if worst is not None and not vel
        else f"${worst} (scenario: price drops {c.get('worst_cut_pct','?')}%)" if worst is not None
        else "N/A"
    )

    lines = [
        f"=== BOOK ARBITRAGE: ISBN {isbn} (ISBN-13: {isbn13}) ===",
        f"",
        f"── SOURCE LISTING ──",
        f"eBay listing: '{c.get('ebay_title','N/A')}' | buy=${c.get('buy_price','?')} | cond={c.get('source_condition','?').upper()}",
        f"eBay seller: {c.get('ebay_seller_name','N/A')} ({c.get('ebay_seller_feedback','?')}% positive)",
        f"eBay description: {(c.get('ebay_description','') or 'none')[:150]}",
        f"",
        f"── AMAZON DATA (VERIFIED FROM SP-API) ──",
        f"Amazon buybox price: ${c.get('amazon_sell_price','?')} ({c.get('buybox_type','?')} buybox)",
        f"Amazon seller count: {c.get('amazon_seller_count','?')}",
        f"Amazon itself selling: {'YES ⚠️' if c.get('amazon_is_sold_by_amazon') else 'No'}",
        f"",
        f"── CALCULATED METRICS (PRE-COMPUTED) ──",
        f"Profit: ${c.get('profit','?')}",
        f"ROI: {c.get('roi_pct','?')}%",
        f"Estimated velocity: {vel_note}",
        f"Worst-case scenario: {worst_note}",
        f"",
        f"── BOOK IDENTITY ──",
        f"Title (verified): {edition.get('google_title','N/A')}",
        f"Author(s): {', '.join(edition.get('authors',[]) or []) or 'N/A'}",
        f"Publisher: {edition.get('publisher','N/A') or 'N/A'}",
        f"Edition year: {edition.get('edition_year','?')}",
        f"Newer edition exists: {'YES ⚠️' if edition.get('has_newer_edition') else 'No/Unknown'}",
        f"Categories/Subjects: {', '.join((edition.get('categories') or [])[:4]) or 'N/A'}",
        f"Dewey Decimal: {edition.get('dewey','N/A') or 'N/A'}",
        f"LC Call Number: {edition.get('lc_class','N/A') or 'N/A'}",
        f"Textbook classification: {'⚠️ LIKELY TEXTBOOK (high semester seasonality, edition risk)' if edition.get('is_textbook_likely') else 'Trade/General book'}",
        f"Page count: {edition.get('page_count','N/A') or 'N/A'}",
        f"Description: {(edition.get('description','') or '')[:200] or 'N/A'}",
        f"Data source: {edition.get('source','unknown')}",
        f"Month: {dt.datetime.utcnow().strftime('%B')} | seasonality={c.get('seasonality_mult','?')}x",
        f"Condition flags: {', '.join(cond['condition_flags']) or 'None'} (score: {cond['condition_score']}/10)",
    ]
    return "\n".join(lines)


def _extract_text(data: Dict[str, Any]) -> str:
    try:
        parts = data["candidates"][0]["content"]["parts"]
        return "\n".join(p.get("text", "") for p in parts if "text" in p)
    except (KeyError, IndexError):
        return json.dumps(data)


def _parse_json(text: str) -> Dict[str, Any]:
    """
    LLM çıktısını JSON'a parse et — 7 yaygın hata modunu dengele:
    1) ```json ... ``` sarması
    2) ``` ... ``` sarması
    3) JSON öncesi/sonrası metin
    4) Trailing comma (basit temizleme)
    5) Kısmi çıktı (en son } bul)
    6) Tamamen metin çıktısı → UNKNOWN fallback
    7) Tek tırnak yerine çift tırnak sorunu
    """
    t = text.strip()

    # Adım 1: Markdown fence temizle
    for fence in ["```json", "```"]:
        if fence in t:
            parts = t.split(fence)
            if len(parts) >= 3:
                t = parts[1].strip()
                break
            else:
                t = t.replace(fence, "").strip()

    # Adım 2: İlk { ... son } arasını al
    s = t.find("{")
    e = t.rfind("}") + 1
    result = None

    if s >= 0 and e > s:
        candidate = t[s:e]

        # Adım 3: Trailing comma temizle (basit regex-free yaklaşım)
        # ",}" ve ",]" pattern'larını temizle
        import re
        candidate = re.sub(r",\s*}", "}", candidate)
        candidate = re.sub(r",\s*]", "]", candidate)

        # Adım 4: Önce direkt parse dene
        try:
            result = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            # Adım 5: Kısmi parse — son geçerli } konumunu bul
            for end in range(len(candidate), 0, -1):
                try:
                    result = json.loads(candidate[:end])
                    if isinstance(result, dict):
                        result["_partial_parse"] = True
                        break
                except (json.JSONDecodeError, ValueError):
                    continue

    if result is None or not isinstance(result, dict):
        result = {"verdict": "UNKNOWN", "summary": text[:300], "parse_error": True}

    # Adım 6: Tam schema normalizasyonu — UI hiçbir zaman eksik key görmez
    _DEFAULTS: Dict[str, Any] = {
        "verdict":            "UNKNOWN",
        "confidence":         0,
        "summary":            "",
        "price_trend":        "UNKNOWN",
        "price_trend_reason": "",
        "risk_level":         "HIGH",
        "risk_factors":       [],
        "recommendation":     "",
        "buy_suggestion":     "",
        "image_verdict":      "UNKNOWN",
        "image_notes":        "",
        "sources_checked":    [],
        "competitors":        [],
    }
    for k, v in _DEFAULTS.items():
        if k not in result:
            result[k] = v

    return result


def _to_isbn13(isbn: str) -> Optional[str]:
    """isbn_utils.to_isbn13'e delegate et — checksum dogrulama dahil."""
    try:
        from app.isbn_utils import to_isbn13
        return to_isbn13(isbn)
    except Exception:
        s = isbn.replace("-", "").replace(" ", "").upper().strip()
        if len(s) == 13 and s.isdigit():
            return s
        return None
