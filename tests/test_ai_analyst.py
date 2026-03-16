from __future__ import annotations
import asyncio, json
import pytest
import app.ai_analyst as ai_analyst
import app.llm_router as llm_router

REQUIRED_PARSE_KEYS = {
    "verdict","confidence","summary","price_trend","price_trend_reason",
    "risk_level","risks","competitors","buy_suggestion",
    "image_verdict","image_notes","sources_checked",
}

BASE_PAYLOADS = [
    {"verdict":"BUY","confidence":80+i,"summary":f"s{i}","price_trend":"STABLE",
     "price_trend_reason":f"t{i}","risk_level":"LOW","risks":[f"r{i}"],
     "competitors":f"{i+2}","buy_suggestion":f"${i+5}",
     "image_verdict":"MATCH","image_notes":f"c{i}","sources_checked":["data"]}
    for i in range(5)
]

def _plain(s): return s
def _fence_json(s): return f"```json\n{s}\n```"
def _fence(s): return f"```\n{s}\n```"
def _prefix(s): return f"Here:\n{s}"
def _suffix(s): return f"{s}\nDone."
def _trailing_comma(s): return s[:-1] + ",}"
def _list_comma(s): return s.replace('"]', '",]')
WRAPPERS = [_plain, _fence_json, _fence, _prefix, _suffix, _trailing_comma, _list_comma]

PARSE_CASES = [
    pytest.param(w(json.dumps(p)), id=f"{w.__name__}-{p['summary']}")
    for w in WRAPPERS for p in BASE_PAYLOADS
]

@pytest.mark.parametrize("raw_text", PARSE_CASES)
def test_parse_json_all_variants_normalized(raw_text):
    result = ai_analyst._parse_json(raw_text)
    assert REQUIRED_PARSE_KEYS.issubset(result.keys())
    assert isinstance(result["risks"], list)
    assert isinstance(result["sources_checked"], list)

@pytest.mark.asyncio
async def test_concurrent_same_key_no_deadlock(monkeypatch):
    ai_analyst._ai_cache_lock = asyncio.Lock()
    monkeypatch.setattr(llm_router, "get_status", lambda: {"stub": {"configured": True}})

    async def fake_edition(isbn, client): return {"edition_year": 2020}
    async def fake_call_llm(prompt, image_b64):
        await asyncio.sleep(0.05)
        return {"verdict":"BUY","confidence":70,"summary":"ok","price_trend":"STABLE",
                "price_trend_reason":"n/a","risk_level":"LOW","risks":[],"competitors":"3",
                "buy_suggestion":"$10","image_verdict":"NO_IMAGE","image_notes":"",
                "sources_checked":["data"]}

    monkeypatch.setattr(ai_analyst, "_check_edition", fake_edition)
    monkeypatch.setattr(ai_analyst, "_call_llm", fake_call_llm)

    candidate = {"buy_price": 24.0, "source_condition": "used", "ebay_title": "Book"}
    t1 = asyncio.create_task(ai_analyst.analyze_isbn("9780132350884", candidate))
    await asyncio.sleep(0.01)
    t2 = asyncio.create_task(ai_analyst.analyze_isbn("9780132350884", candidate))

    try:
        results = await asyncio.wait_for(asyncio.gather(t1, t2), timeout=2.0)
    except asyncio.TimeoutError:
        t1.cancel(); t2.cancel()
        await asyncio.gather(t1, t2, return_exceptions=True)
        pytest.fail("Deadlock: concurrent analyze_isbn timed out")
    assert len(results) == 2

@pytest.mark.asyncio
async def test_provider_failure_returns_full_schema(monkeypatch):
    ai_analyst._ai_cache_lock = asyncio.Lock()
    monkeypatch.setattr(llm_router, "get_status", lambda: {"stub": {"configured": True}})
    async def fake_edition(isbn, client): return {}
    async def fake_route(**kwargs): raise RuntimeError("provider down")
    monkeypatch.setattr(ai_analyst, "_check_edition", fake_edition)
    monkeypatch.setattr(llm_router, "route", fake_route)

    result = await ai_analyst.analyze_isbn(
        "9780132350884", {"buy_price": 18.0, "source_condition": "used", "ebay_title": "Book"})
    assert REQUIRED_PARSE_KEYS.issubset(result.keys())

@pytest.mark.asyncio
async def test_cache_different_sellers_get_different_verdicts(monkeypatch):
    ai_analyst._ai_cache_lock = asyncio.Lock()
    monkeypatch.setattr(llm_router, "get_status", lambda: {"stub": {"configured": True}})
    async def fake_edition(isbn, client): return {}
    async def fake_call_llm(prompt, image_b64):
        seller = prompt.split("eBay seller: ", 1)[-1].split(" ", 1)[0] if "eBay seller:" in prompt else "X"
        return {"verdict":"WATCH","confidence":55,"summary":seller,"price_trend":"STABLE",
                "price_trend_reason":"n/a","risk_level":"MEDIUM","risks":[],"competitors":"5",
                "buy_suggestion":"$11","image_verdict":"NO_IMAGE","image_notes":"",
                "sources_checked":["data"]}
    monkeypatch.setattr(ai_analyst, "_check_edition", fake_edition)
    monkeypatch.setattr(ai_analyst, "_call_llm", fake_call_llm)

    r1 = await ai_analyst.analyze_isbn("9780132350884",
        {"buy_price":21.0,"source_condition":"used","ebay_title":"Book A","ebay_seller_name":"alpha","item_id":"item1"})
    r2 = await ai_analyst.analyze_isbn("9780132350884",
        {"buy_price":24.99,"source_condition":"used","ebay_title":"Book B","ebay_seller_name":"bravo","item_id":"item2"})
    assert r1["summary"] != r2["summary"]
