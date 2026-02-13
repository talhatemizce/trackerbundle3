import os
import requests
from dotenv import load_dotenv
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.credentials import Credentials

load_dotenv(dotenv_path=".env")
LWA_TOKEN_URL = "https://api.amazon.com/auth/o2/token"

def _lwa_access_token() -> str:
    r = requests.post(
        LWA_TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": os.environ["LWA_REFRESH_TOKEN"],
            "client_id": os.environ["LWA_CLIENT_ID"],
            "client_secret": os.environ["LWA_CLIENT_SECRET"],
        },
        headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["access_token"]

def _sign(method: str, url: str, headers: dict) -> dict:
    creds = Credentials(os.environ["AWS_ACCESS_KEY_ID"], os.environ["AWS_SECRET_ACCESS_KEY"])
    req = AWSRequest(method=method, url=url, data=None, headers=headers)
    SigV4Auth(creds, "execute-api", os.environ.get("AWS_REGION", "us-east-1")).add_auth(req)
    return dict(req.headers)

def _money(x) -> float:
    if not x:
        return 0.0
    return float(x.get("Amount", 0.0) or 0.0)

def _normalize_offers(payload: dict) -> list[dict]:
    offers = payload.get("Offers") or []
    rows = []
    for o in offers:
        lp = _money(o.get("ListingPrice"))
        ship = _money(o.get("Shipping"))
        total = lp + ship
        rows.append({
            "total": round(total, 2),
            "price": round(lp, 2),
            "ship": round(ship, 2),
            "sellerId": o.get("SellerId"),
            "fba": bool(o.get("IsFulfilledByAmazon")),
            "prime": bool((o.get("PrimeInformation") or {}).get("IsPrime")),
            "featured": bool(o.get("IsFeaturedMerchant")),
            "buybox": bool(o.get("IsBuyBoxWinner")),
            "pos": (o.get("SellerFeedbackRating") or {}).get("SellerPositiveFeedbackRating"),
            "count": (o.get("SellerFeedbackRating") or {}).get("FeedbackCount"),
        })
    rows.sort(key=lambda x: x["total"])
    return rows

def _fetch_offers(asin: str, condition: str, marketplace_id: str) -> dict:
    endpoint = os.environ["SPAPI_ENDPOINT"].rstrip("/")
    access = _lwa_access_token()
    url = f"{endpoint}/products/pricing/v0/items/{asin}/offers"
    params = {"MarketplaceId": marketplace_id, "ItemCondition": condition}

    prepared = requests.Request("GET", url, params=params).prepare()
    base = {
        "host": prepared.url.split("/")[2],
        "x-amz-access-token": access,
        "content-type": "application/json",
    }
    headers = _sign("GET", prepared.url, base)

    r = requests.get(prepared.url, headers=headers, timeout=30)
    r.raise_for_status()

    payload = (r.json() or {}).get("payload") or {}
    rows = _normalize_offers(payload)
    buybox = next((x for x in rows if x["buybox"]), None)

    return {"count": len(rows), "buybox": buybox, "top2": rows[:2]}

def get_top2_new_used(asin: str, marketplace_id: str | None = None) -> dict:
    mkt = (marketplace_id or os.environ.get("SPAPI_MARKETPLACE_ID", "ATVPDKIKX0DER")).strip()
    return {
        "asin": asin,
        "marketplaceId": mkt,
        "new": _fetch_offers(asin, "New", mkt),
        "used": _fetch_offers(asin, "Used", mkt),
    }
