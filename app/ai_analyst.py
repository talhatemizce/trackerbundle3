"""
AI Analyst — ISBN için Gemini Flash + Google Search grounding ile analiz.
httpx ile doğrudan Gemini API çağrısı (SDK gerektirmez), tamamen ücretsiz.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

GEMINI_API = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-lite:generateContent"

SYSTEM_PROMPT = """You are a book arbitrage expert. The user will give you an ISBN and current price/market data.

Your job: Is it worth buying this book on eBay and selling it on Amazon?

Reply ONLY with the following JSON. No other text, no markdown fences:
{
  "verdict": "BUY or PASS or WATCH",
  "confidence": number 0-100,
  "summary": "2-3 sentence summary",
  "price_trend": "RISING or STABLE or DECLINING or UNKNOWN",
  "price_trend_reason": "short explanation",
  "risk_level": "LOW or MEDIUM or HIGH",
  "risks": ["risk1", "risk2"],
  "competitors": "short comment on competing sellers on eBay and Amazon",
  "buy_suggestion": "max price to pay and preferred condition",
  "sources_checked": ["source1", "source2"]
}"""


async def analyze_isbn(isbn: str, candidate: Dict[str, Any]) -> Dict[str, Any]:
    s = get_settings()
    if not s.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY ayarlanmamış — /etc/trackerbundle.env dosyasına ekle: GEMINI_API_KEY=AIza...")

    user_msg = _build_user_message(isbn, candidate)

    payload = {
        "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": user_msg}]}],
        "tools": [{"google_search": {}}],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 1500,
        },
    }

    url = f"{GEMINI_API}?key={s.gemini_api_key}"

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(url, json=payload, headers={"Content-Type": "application/json"})
        if resp.status_code != 200:
            logger.error("Gemini API error %d: %s", resp.status_code, resp.text[:300])
            resp.raise_for_status()
        data = resp.json()

    text = _extract_text(data)
    analysis = _parse_json_response(text)
    analysis["isbn"] = isbn
    return analysis


def _build_user_message(isbn: str, c: Dict[str, Any]) -> str:
    isbn13 = _to_isbn13(isbn) or isbn
    lines = [
        f"ISBN: {isbn} (ISBN-13: {isbn13})",
        f"eBay listing: {(c.get('source_condition') or '?').upper()} condition, buy price ${c.get('buy_price','?')}",
        f"Amazon current price: ${c.get('amazon_sell_price','?')} ({c.get('buybox_type','?')} buybox)",
        f"Calculated profit: ${c.get('profit','?')} ({c.get('roi_pct','?')}% ROI)",
        f"Confidence score: {c.get('confidence','?')}/100",
        f"Estimated monthly sales: {c.get('velocity','?')} units",
        f"Worst case profit: ${c.get('worst_case_profit','?')}",
        "",
        f"Please search the web for ISBN {isbn13}: Amazon and eBay price history, "
        "number of sellers, BSR info, new edition released, popularity. "
        "Then reply with ONLY the JSON analysis.",
    ]
    return "\n".join(lines)


def _extract_text(data: Dict[str, Any]) -> str:
    try:
        parts = data["candidates"][0]["content"]["parts"]
        return "\n".join(p.get("text", "") for p in parts if "text" in p)
    except (KeyError, IndexError):
        return json.dumps(data)


def _parse_json_response(text: str) -> Dict[str, Any]:
    text = text.strip()
    for fence in ["```json", "```"]:
        if fence in text:
            parts = text.split(fence)
            text = parts[1] if len(parts) >= 3 else text.replace(fence, "")
    text = text.strip()
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end])
        except Exception:
            pass
    return {"verdict": "UNKNOWN", "summary": text[:500], "parse_error": True}


def _to_isbn13(isbn: str) -> Optional[str]:
    s = isbn.replace("-", "").replace(" ", "").upper().strip()
    if len(s) == 13:
        return s
    if len(s) != 10:
        return None
    core = "978" + s[:9]
    total = sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(core))
    check = (10 - (total % 10)) % 10
    return core + str(check)
