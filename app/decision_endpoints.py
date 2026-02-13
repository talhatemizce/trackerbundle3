from fastapi import APIRouter, HTTPException, Query
import httpx
from app.rules_store import effective_limit

router = APIRouter(prefix="/decide", tags=["Decision"])

SPAPI_BASE = "http://127.0.0.1/spapi"  # nginx üzerinden

def _money_int(x):
    try:
        return int(round(float(x)))
    except Exception:
        return None

def _round_top2(items):
    out = []
    for it in (items or []):
        if not isinstance(it, dict):
            continue
        it2 = dict(it)
        for k in ("total","price","ship"):
            if k in it2 and it2[k] is not None:
                it2[k] = _money_int(it2[k])
        out.append(it2)
    return out


@router.get("/asin")
async def decide_asin(
    asin: str = Query(...),
    isbn: str | None = Query(None),
):
    try:
        async with httpx.AsyncClient(timeout=25) as c:
            r = await c.get(f"{SPAPI_BASE}/offers/top2", params={"asin": asin})
        if r.status_code != 200:
            raise HTTPException(status_code=502, detail=f"spapi status {r.status_code}: {r.text[:200]}")
        data = r.json()

        out = {"asin": asin, "isbn": isbn, "marketplaceId": data.get("marketplaceId")}

        new_top2 = _round_top2((data.get("new") or {}).get("top2") or [])
        new_best = new_top2[0].get("total") if new_top2 else None
        new_rule = effective_limit(isbn, "brand_new")
        out["new"] = {
            "best_total": new_best,
            "limit": _money_int(new_rule["limit"]),
            "source": new_rule.get("source"),
            "decision": "BUY" if (new_best is not None and float(new_best) <= float(new_rule["limit"])) else "SKIP",
            "top2": new_top2,
        }

        used_top2 = _round_top2((data.get("used") or {}).get("top2") or [])
        used_best = used_top2[0].get("total") if used_top2 else None
        used_rule = effective_limit(isbn, "used_all")
        out["used"] = {
            "best_total": used_best,
            "limit": _money_int(used_rule["limit"]),
            "source": used_rule.get("source"),
            "decision": "BUY" if (used_best is not None and float(used_best) <= float(used_rule["limit"])) else "SKIP",
            "top2": used_top2,
        }

        return out

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
