"""
AI Analyst — ISBN için Claude + web search ile kapsamlı kitap analizi.
httpx ile doğrudan Anthropic API çağrısı (SDK gerektirmez).
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

ANTHROPIC_API = "https://api.anthropic.com/v1/messages"
MODEL = "claude-sonnet-4-20250514"


SYSTEM_PROMPT = """Sen bir kitap arbitraj uzmanısın. Kullanıcı sana bir ISBN ve o kitap için mevcut fiyat/piyasa verilerini verecek.

Görevin: Bu kitabı eBay'den alıp Amazon'da satmak mantıklı mı? Kısa, net, İngilizce analiz yap.

Şu başlıkları kapsayan JSON döndür (başka bir şey yazma, sadece JSON):
{
  "verdict": "BUY" | "PASS" | "WATCH",
  "confidence": 0-100,
  "summary": "2-3 cümle özet",
  "price_trend": "RISING" | "STABLE" | "DECLINING" | "UNKNOWN",
  "price_trend_reason": "kısa açıklama",
  "risk_level": "LOW" | "MEDIUM" | "HIGH",
  "risks": ["risk1", "risk2"],
  "competitors": "eBay ve Amazon'daki rakip satıcı durumu hakkında kısa yorum",
  "buy_suggestion": "Ne kadar max ödenmeli, hangi kondisyon tercih edilmeli",
  "sources_checked": ["kaynak1", "kaynak2"]
}

Web arama yap, kitabın güncel fiyat geçmişine, popülerliğine, edisyon bilgisine bak."""


async def analyze_isbn(
    isbn: str,
    candidate: Dict[str, Any],
) -> Dict[str, Any]:
    """
    ISBN için AI analizi yap.
    candidate: ArbResult dict (buy_price, amazon_sell_price, profit, roi_pct, source, source_condition, vb.)
    """
    s = get_settings()
    if not s.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY ayarlanmamış — /etc/trackerbundle.env dosyasına ekle")

    # Kullanıcı mesajını oluştur
    user_msg = _build_user_message(isbn, candidate)

    payload = {
        "model": MODEL,
        "max_tokens": 1500,
        "system": SYSTEM_PROMPT,
        "tools": [{"type": "web_search_20250305", "name": "web_search"}],
        "messages": [{"role": "user", "content": user_msg}],
    }

    headers = {
        "x-api-key": s.anthropic_api_key,
        "anthropic-version": "2023-06-01",
        "anthropic-beta": "web-search-2025-03-05",
        "content-type": "application/json",
    }

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(ANTHROPIC_API, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    # Extract text from response (may contain tool_use blocks)
    text = _extract_text(data)
    analysis = _parse_json_response(text)
    analysis["isbn"] = isbn
    analysis["raw_text"] = text
    return analysis


def _build_user_message(isbn: str, c: Dict[str, Any]) -> str:
    isbn13 = _to_isbn13(isbn)
    lines = [
        f"ISBN: {isbn} (ISBN-13: {isbn13})",
        f"eBay listing: {c.get('source_condition','?').upper()} kondisyon, alım fiyatı ${c.get('buy_price','?')}",
        f"Amazon mevcut fiyat: ${c.get('amazon_sell_price','?')} ({c.get('buybox_type','?')} buybox)",
        f"Hesaplanan kar: ${c.get('profit','?')} (%{c.get('roi_pct','?')} ROI)",
        f"Güven skoru: {c.get('confidence','?')}/100",
        f"Tahmini aylık satış: {c.get('velocity','?')} adet",
        f"Worst case kar: ${c.get('worst_case_profit','?')}",
        "",
        "Lütfen bu kitabı web'de ara: fiyat geçmişi, popülerlik, yeni/kullanılmış fiyatlar, "
        "kaç satıcı var, BSR geçmişi varsa, edisyon değişikliği riski var mı. "
        "Sonra yukarıdaki JSON formatında analiz ver.",
    ]
    return "\n".join(lines)


def _extract_text(data: Dict[str, Any]) -> str:
    """Response content bloklarından text'i birleştir."""
    parts = []
    for block in data.get("content", []):
        if block.get("type") == "text":
            parts.append(block["text"])
    return "\n".join(parts)


def _parse_json_response(text: str) -> Dict[str, Any]:
    """JSON bloğunu parse et, başarısız olursa raw döndür."""
    text = text.strip()
    # ```json ... ``` fences varsa temizle
    for fence in ["```json", "```"]:
        if fence in text:
            text = text.split(fence)[-2] if text.count(fence) >= 2 else text.replace(fence, "")
    text = text.strip()
    # Find first { ... }
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
