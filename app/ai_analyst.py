"""
AI Analyst v2 — Kapsamlı kitap arbitraj analizi.
Gemini 2.5 Flash-Lite + Google Search + Vision (kapak resmi doğrulama).
Google Books API ile yeni baskı tespiti.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Any, Dict, Optional

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
MODEL = "gemini-2.5-flash-lite"
GOOGLE_BOOKS_API = "https://www.googleapis.com/books/v1/volumes"


# ─── Google Books: yeni baskı tespiti ─────────────────────────────────────────

async def _check_edition(isbn: str, client: httpx.AsyncClient) -> Dict[str, Any]:
    """Google Books API ile kitabın yayın yılını ve başka baskılarını kontrol et."""
    isbn13 = _to_isbn13(isbn) or isbn
    try:
        r = await client.get(
            GOOGLE_BOOKS_API,
            params={"q": f"isbn:{isbn13}", "maxResults": 1, "fields": "items(volumeInfo(title,authors,publishedDate,industryIdentifiers))"},
            timeout=10,
        )
        if r.status_code != 200:
            return {}
        items = r.json().get("items") or []
        if not items:
            return {}
        vi = items[0].get("volumeInfo", {})
        pub_date = vi.get("publishedDate", "")
        year = None
        if pub_date and len(pub_date) >= 4:
            try:
                year = int(pub_date[:4])
            except:
                pass
        title = vi.get("title", "")
        # Daha yeni baskı var mı? → Aynı başlığı daha yeni tarihle ara
        newer = False
        if title and year:
            r2 = await client.get(
                GOOGLE_BOOKS_API,
                params={"q": f'intitle:"{title[:30]}"', "maxResults": 5, "orderBy": "newest",
                        "fields": "items(volumeInfo(publishedDate,industryIdentifiers))"},
                timeout=10,
            )
            if r2.status_code == 200:
                for item in (r2.json().get("items") or []):
                    pd = (item.get("volumeInfo") or {}).get("publishedDate", "")
                    if pd and len(pd) >= 4:
                        try:
                            if int(pd[:4]) > year:
                                newer = True
                                break
                        except:
                            pass
        return {"edition_year": year, "has_newer_edition": newer, "google_title": title}
    except Exception as e:
        logger.debug("Google Books error: %s", e)
        return {}


# ─── eBay kapak resmi indirme ──────────────────────────────────────────────────

async def _fetch_image_b64(url: str, client: httpx.AsyncClient) -> Optional[str]:
    """Resmi indir, base64 döndür."""
    if not url:
        return None
    try:
        r = await client.get(url, timeout=15, follow_redirects=True)
        if r.status_code == 200 and "image" in r.headers.get("content-type", ""):
            return base64.standard_b64encode(r.content).decode()
    except Exception as e:
        logger.debug("Image fetch error: %s", e)
    return None


# ─── Kondisyon uyumsuzluğu analizi (hızlı, local) ────────────────────────────

def _condition_mismatch_score(title: str, description: str, declared_condition: str) -> Dict[str, Any]:
    """
    Başlık/açıklama vs. declared condition karşılaştır.
    Red flag kelimeleri tespit et.
    """
    combined = (title + " " + description).lower()
    red_flags = []

    damage_words = ["writing", "written", "highlight", "underlining", "notes", "stain",
                    "damage", "torn", "missing", "water", "mold", "smell", "worn"]
    for w in damage_words:
        if w in combined:
            red_flags.append(f"'{w}' in listing")

    # Kondisyon çelişkisi
    if declared_condition == "new" and any(w in combined for w in ["used", "pre-owned", "second hand"]):
        red_flags.append("listed as NEW but description says used")
    if declared_condition == "used" and "brand new" in combined and "sealed" in combined:
        red_flags.append("might actually be new/sealed")

    risk = "HIGH" if len(red_flags) >= 2 else "MEDIUM" if red_flags else "LOW"
    return {"condition_flags": red_flags, "condition_risk": risk}


# ─── Ana analiz fonksiyonu ────────────────────────────────────────────────────

async def analyze_isbn(isbn: str, candidate: Dict[str, Any]) -> Dict[str, Any]:
    s = get_settings()
    if not s.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY ayarlanmamış — /etc/trackerbundle.env → GEMINI_API_KEY=AIza...")

    key = s.gemini_api_key
    isbn13 = _to_isbn13(isbn) or isbn

    async with httpx.AsyncClient(timeout=30) as client:
        # Paralel: Google Books + resim indirme
        edition_task = _check_edition(isbn, client)
        img_url = candidate.get("ebay_image_url", "")
        image_task = _fetch_image_b64(img_url, client) if img_url else asyncio.sleep(0, result=None)

        edition_data, image_b64 = await asyncio.gather(edition_task, image_task)

    # Local kondisyon analizi
    cond_analysis = _condition_mismatch_score(
        candidate.get("ebay_title", ""),
        candidate.get("ebay_description", ""),
        candidate.get("source_condition", "used"),
    )

    # Gemini prompt oluştur
    prompt = _build_prompt(isbn, isbn13, candidate, edition_data, cond_analysis)

    # Gemini API çağrısı (Vision varsa multimodal, yoksa text-only)
    gemini_result = await _call_gemini(key, prompt, image_b64, isbn13)

    # Tüm verileri birleştir
    result = {**gemini_result}
    result["isbn"] = isbn
    result["edition_year"] = edition_data.get("edition_year")
    result["has_newer_edition"] = edition_data.get("has_newer_edition")
    result["google_title"] = edition_data.get("google_title", "")
    result["condition_flags"] = cond_analysis["condition_flags"]
    result["condition_risk"] = cond_analysis["condition_risk"]
    result["image_verified"] = bool(image_b64)
    result["amazon_seller_count"] = candidate.get("amazon_seller_count")
    result["amazon_is_sold_by_amazon"] = candidate.get("amazon_is_sold_by_amazon", False)
    result["seasonality_mult"] = candidate.get("seasonality_mult")

    # Newer edition → upgrade risk level
    if edition_data.get("has_newer_edition") and result.get("risk_level") == "LOW":
        result["risk_level"] = "MEDIUM"
        risks = result.get("risks") or []
        risks.insert(0, "⚠️ Newer edition detected — demand may shift")
        result["risks"] = risks

    # Amazon satıyor → risk
    if candidate.get("amazon_is_sold_by_amazon"):
        risks = result.get("risks") or []
        risks.insert(0, "🚫 Amazon itself is selling — buybox competition very hard")
        result["risks"] = risks
        if result.get("verdict") == "BUY":
            result["verdict"] = "WATCH"

    return result


async def _call_gemini(key: str, prompt: str, image_b64: Optional[str], isbn13: str) -> Dict[str, Any]:
    """Gemini API çağrısı — Vision veya text-only."""
    url = f"{GEMINI_API_BASE}/{MODEL}:generateContent?key={key}"

    # Content parts
    parts = []
    if image_b64:
        parts.append({"inline_data": {"mime_type": "image/jpeg", "data": image_b64}})
    parts.append({"text": prompt})

    payload = {
        "contents": [{"role": "user", "parts": parts}],
        "tools": [{"google_search": {}}],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 1500},
        "system_instruction": {"parts": [{"text": _system_prompt(bool(image_b64))}]},
    }

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(url, json=payload, headers={"Content-Type": "application/json"})
        if resp.status_code != 200:
            raise RuntimeError(f"Gemini API {resp.status_code}: {resp.text[:200]}")
        data = resp.json()

    text = _extract_text(data)
    return _parse_json(text)


def _system_prompt(has_image: bool) -> str:
    img_instruction = """
Also analyze the provided eBay listing image:
- Does the book cover match this ISBN? Look for title, author, edition info visible on cover.
- Does condition look accurate vs declared condition?
- Add "image_verdict": "MATCH" | "MISMATCH" | "UNCERTAIN" to JSON.
- Add "image_notes": short observation about the image.
""" if has_image else '- Add "image_verdict": "NO_IMAGE", "image_notes": "No image provided".'

    return f"""You are a book arbitrage expert. Analyze whether buying this book on eBay and selling on Amazon is profitable.

{img_instruction}

Search the web for: current Amazon price, eBay sold listings, BSR trend, number of sellers, any edition changes.

Reply ONLY with this exact JSON (no other text, no markdown):
{{
  "verdict": "BUY or PASS or WATCH",
  "confidence": 0-100,
  "summary": "2-3 sentence analysis",
  "price_trend": "RISING or STABLE or DECLINING or UNKNOWN",
  "price_trend_reason": "brief explanation",
  "risk_level": "LOW or MEDIUM or HIGH",
  "risks": ["risk1", "risk2"],
  "competitors": "comment on competing sellers",
  "buy_suggestion": "max price and preferred condition",
  "image_verdict": "MATCH or MISMATCH or UNCERTAIN or NO_IMAGE",
  "image_notes": "observation",
  "sources_checked": ["source1", "source2"]
}}"""


def _build_prompt(isbn: str, isbn13: str, c: Dict[str, Any],
                  edition: Dict[str, Any], cond: Dict[str, Any]) -> str:
    import datetime as dt
    month_name = dt.datetime.utcnow().strftime("%B")

    lines = [
        f"=== BOOK ARBITRAGE ANALYSIS REQUEST ===",
        f"ISBN-10: {isbn} | ISBN-13: {isbn13}",
        f"",
        f"--- eBay Listing ---",
        f"Title: {c.get('ebay_title','unknown')}",
        f"Buy price (incl. shipping): ${c.get('buy_price','?')}",
        f"Declared condition: {c.get('source_condition','?').upper()}",
        f"Seller: {c.get('ebay_seller_name','?')} ({c.get('ebay_seller_feedback','?')}% positive)",
        f"Description: {c.get('ebay_description','') or 'none'}",
        f"",
        f"--- Amazon Current Data ---",
        f"Sell price: ${c.get('amazon_sell_price','?')} ({c.get('buybox_type','?')} buybox)",
        f"Calculated profit: ${c.get('profit','?')} ({c.get('roi_pct','?')}% ROI)",
        f"Seller count: {c.get('amazon_seller_count','?')}",
        f"Amazon is seller: {'YES ⚠️' if c.get('amazon_is_sold_by_amazon') else 'No'}",
        f"",
        f"--- Pre-computed Analysis ---",
        f"Confidence score: {c.get('confidence','?')}/100",
        f"Est. monthly velocity: {c.get('velocity','?')} units",
        f"Worst case profit: ${c.get('worst_case_profit','?')}",
        f"Current month: {month_name} (seasonality: {c.get('seasonality_mult','?')}x)",
        f"",
        f"--- Edition Check (Google Books) ---",
        f"Publication year: {edition.get('edition_year','unknown')}",
        f"Newer edition detected: {'YES ⚠️' if edition.get('has_newer_edition') else 'No'}",
        f"",
        f"--- Condition Consistency Check ---",
        f"Red flags found: {', '.join(cond['condition_flags']) if cond['condition_flags'] else 'None'}",
        f"",
        f"Please search the web to verify current pricing and demand, then provide your JSON analysis.",
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
    start, end = t.find("{"), t.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            return json.loads(t[start:end])
        except Exception:
            pass
    return {"verdict": "UNKNOWN", "summary": t[:300], "parse_error": True}


def _to_isbn13(isbn: str) -> Optional[str]:
    s = isbn.replace("-", "").replace(" ", "").upper().strip()
    if len(s) == 13:
        return s
    if len(s) != 10:
        return None
    core = "978" + s[:9]
    total = sum(int(ch) * (1 if i % 2 == 0 else 3) for i, ch in enumerate(core))
    check = (10 - (total % 10)) % 10
    return core + str(check)
