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

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
MODEL = "gemini-2.5-flash-lite"
GOOGLE_BOOKS_API = "https://www.googleapis.com/books/v1/volumes"
OPEN_LIBRARY_API = "https://openlibrary.org/api/books"


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
    isbn13 = _to_isbn13(isbn) or isbn

    # 1. Google Books
    try:
        r = await client.get(
            GOOGLE_BOOKS_API,
            params={"q": f"isbn:{isbn13}", "maxResults": 1,
                    "fields": "items(volumeInfo(title,authors,publishedDate))"},
            timeout=10,
        )
        if r.status_code == 200:
            items = r.json().get("items") or []
            if items:
                vi = items[0].get("volumeInfo", {})
                pub_date = vi.get("publishedDate", "")
                year = int(pub_date[:4]) if pub_date and len(pub_date) >= 4 else None
                title = vi.get("title", "")
                authors = vi.get("authors") or []

                # Aynı başlık + yazar için daha yeni baskı var mı?
                newer = False
                if title and year:
                    query = f'intitle:"{title[:25]}"'
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
                            pd = (item.get("volumeInfo") or {}).get("publishedDate", "")
                            # Farklı ISBN mi? (aynı baskı değil)
                            idents = (item.get("volumeInfo") or {}).get("industryIdentifiers") or []
                            item_isbns = [x.get("identifier","") for x in idents]
                            if isbn13 in item_isbns or isbn in item_isbns:
                                continue  # Aynı kitap
                            if pd and len(pd) >= 4:
                                try:
                                    if int(pd[:4]) > year:
                                        newer = True
                                        break
                                except:
                                    pass
                return {"edition_year": year, "has_newer_edition": newer,
                        "google_title": title, "source": "google_books"}
    except Exception as e:
        logger.debug("Google Books error: %s", e)

    # 2. Open Library yedek
    try:
        r = await client.get(
            OPEN_LIBRARY_API,
            params={"bibkeys": f"ISBN:{isbn13}", "format": "json", "jscmd": "data"},
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            book = data.get(f"ISBN:{isbn13}") or {}
            pub_date = book.get("publish_date", "")
            year = None
            for chunk in pub_date.split():
                try:
                    y = int(chunk)
                    if 1900 < y < 2030:
                        year = y
                        break
                except:
                    pass
            return {"edition_year": year, "has_newer_edition": None,
                    "google_title": book.get("title",""), "source": "open_library"}
    except Exception as e:
        logger.debug("Open Library error: %s", e)

    return {}


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

    # ── BUY override: rakamlar çok iyi + risk düşük ──
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

    return result


# ─── Ana analiz ────────────────────────────────────────────────────────────────

async def analyze_isbn(isbn: str, candidate: Dict[str, Any]) -> Dict[str, Any]:
    s = get_settings()
    if not s.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY ayarlanmamış — /etc/trackerbundle.env → GEMINI_API_KEY=AIza...")

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
    gemini_result = await _call_gemini(s.gemini_api_key, prompt, image_b64)

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
    })

    return gemini_result


async def _call_gemini(key: str, prompt: str, image_b64: Optional[str]) -> Dict[str, Any]:
    url = f"{GEMINI_API_BASE}/{MODEL}:generateContent?key={key}"
    parts = []
    if image_b64:
        parts.append({"inline_data": {"mime_type": "image/jpeg", "data": image_b64}})
    parts.append({"text": prompt})

    payload = {
        "contents": [{"role": "user", "parts": parts}],
        "tools": [{"google_search": {}}],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 1200},
        "system_instruction": {"parts": [{"text": _system_prompt(bool(image_b64))}]},
    }

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(url, json=payload, headers={"Content-Type": "application/json"})
        if resp.status_code != 200:
            raise RuntimeError(f"Gemini API {resp.status_code}: {resp.text[:200]}")

    text = _extract_text(resp.json())
    return _parse_json(text)


def _system_prompt(has_image: bool) -> str:
    img_part = """
Analyze the provided eBay listing image:
- Does the cover match this book (title/author visible)?
- Does condition look accurate?
- Add "image_verdict": "MATCH"|"MISMATCH"|"UNCERTAIN"
- Add "image_notes": brief observation
""" if has_image else '- Set "image_verdict": "NO_IMAGE", "image_notes": ""'

    return f"""You are a book arbitrage expert. Your job: verify the eBay listing matches the intended book, then assess profitability.

CRITICAL — ISBN CONFLICT DETECTION:
If your web search finds multiple different books sharing this ISBN (data error, misprint, or ISBN reuse):
- Use the eBay listing title and image to determine WHICH book is actually being sold
- Set image_verdict = "MISMATCH" if the listing appears to be a different book than the Amazon data
- Set risk_level = "HIGH" and verdict = "PASS" if you cannot confirm which book it is
- Explain the conflict clearly in "summary"

{img_part}

STEP 1: Search the web for this ISBN — confirm exact title, author, edition.
STEP 2: Check if multiple different books share this ISBN (conflict = high risk).
STEP 3: Verify eBay listing title/image matches the confirmed book identity.
STEP 4: Check current Amazon price, BSR, seller count, demand.
STEP 5: Give your verdict.

Reply ONLY with this JSON (no markdown, no extra text):
{{
  "verdict": "BUY or PASS or WATCH",
  "confidence": 0-100,
  "summary": "2-3 sentence analysis. If ISBN conflict found, explain it here.",
  "price_trend": "RISING or STABLE or DECLINING or UNKNOWN",
  "price_trend_reason": "brief explanation",
  "risk_level": "LOW or MEDIUM or HIGH",
  "risks": [],
  "competitors": "comment on competing sellers on Amazon/eBay",
  "buy_suggestion": "max price and preferred condition, or SKIP if ISBN conflict",
  "image_verdict": "MATCH or MISMATCH or UNCERTAIN or NO_IMAGE",
  "image_notes": "what you see in the image, or explain ISBN conflict if relevant",
  "isbn_conflict": false,
  "isbn_conflict_note": "explain if multiple books share this ISBN",
  "sources_checked": []
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
        f"eBay listing: '{c.get('ebay_title','?')}' | buy=${c.get('buy_price','?')} | cond={c.get('source_condition','?').upper()}",
        f"eBay seller: {c.get('ebay_seller_name','?')} ({c.get('ebay_seller_feedback','?')}% positive)",
        f"eBay description: {(c.get('ebay_description','') or 'none')[:150]}",
        f"",
        f"Amazon current: ${c.get('amazon_sell_price','?')} ({c.get('buybox_type','?')} buybox)",
        f"Calculated profit: ${c.get('profit','?')} ({c.get('roi_pct','?')}% ROI) — based on current buybox",
        f"Amazon sellers: {c.get('amazon_seller_count','?')} | Amazon itself selling: {'YES' if c.get('amazon_is_sold_by_amazon') else 'No'}",
        f"",
        f"⚠ IMPORTANT — these are pre-computed MODEL ESTIMATES, NOT real market data:",
        f"  velocity (estimated): {vel_note}",
        f"  worst-case scenario: {worst_note}",
        f"  Do NOT use these to set your buy_suggestion — use real web search data instead.",
        f"",
        f"Month: {dt.datetime.utcnow().strftime('%B')} | seasonality={c.get('seasonality_mult','?')}x",
        f"Edition: {edition.get('edition_year','?')} | Newer edition: {'YES ⚠️' if edition.get('has_newer_edition') else 'No/Unknown'}",
        f"Condition flags: {', '.join(cond['condition_flags']) or 'None'} (score: {cond['condition_score']}/10)",
        f"",
        f"SEARCH TASKS:",
        f"1. Search '{isbn13} amazon price history' — find the real 90-day price floor",
        f"2. Search '{(c.get('ebay_title') or isbn)[:40]} amazon sales rank' — confirm demand",
        f"3. If Amazon price is suspiciously high or low, check if it's a spike",
        f"4. Base buy_suggestion on REAL historical price data you find, not the model estimate",
    ]
    return "\n".join(lines)


def _extract_text(data: Dict[str, Any]) -> str:
    try:
        parts = data["candidates"][0]["content"]["parts"]
        return "\n".join(p.get("text", "") for p in parts if "text" in p)
    except (KeyError, IndexError):
        return json.dumps(data)


def _parse_json(text: str) -> Dict[str, Any]:
    t = text.strip()
    for fence in ["```json", "```"]:
        if fence in t:
            parts = t.split(fence)
            t = parts[1] if len(parts) >= 3 else t.replace(fence, "")
    t = t.strip()
    s, e = t.find("{"), t.rfind("}") + 1
    result = None
    if s >= 0 and e > s:
        try:
            result = json.loads(t[s:e])
        except:
            pass
    if result is None:
        result = {"verdict": "UNKNOWN", "summary": t[:300], "parse_error": True}
    # Schema normalization — eksik alanlar default değerle doldur
    _DEFAULTS = {
        "verdict": "UNKNOWN", "summary": "", "price_trend": "UNKNOWN",
        "price_trend_reason": "", "risk_level": "HIGH",
        "risk_factors": [], "recommendation": "",
    }
    for k, v in _DEFAULTS.items():
        if k not in result:
            result[k] = v
    return result


def _to_isbn13(isbn: str) -> Optional[str]:
    s = isbn.replace("-", "").replace(" ", "").upper().strip()
    if len(s) == 13 and s.isdigit():
        return s
    if len(s) != 10:
        return None
    # ISBN-10 sadece rakam+X kabul et
    if not all(c.isdigit() or (c == "X" and i == 9) for i, c in enumerate(s)):
        return None
    try:
        core = "978" + s[:9]
        total = sum(int(ch) * (1 if i % 2 == 0 else 3) for i, ch in enumerate(core))
        return core + str((10 - (total % 10)) % 10)
    except (ValueError, TypeError):
        return None
