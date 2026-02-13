import os, requests
from dotenv import load_dotenv
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.credentials import Credentials

load_dotenv(dotenv_path=".env")
LWA_TOKEN_URL = "https://api.amazon.com/auth/o2/token"

def lwa_token():
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

def sign(method, url, headers):
    creds = Credentials(os.environ["AWS_ACCESS_KEY_ID"], os.environ["AWS_SECRET_ACCESS_KEY"])
    req = AWSRequest(method=method, url=url, data=None, headers=headers)
    SigV4Auth(creds, "execute-api", os.environ.get("AWS_REGION","us-east-1")).add_auth(req)
    return dict(req.headers)

def money(x):
    if not x: return None
    return float(x.get("Amount", 0.0))

def main():
    endpoint = os.environ["SPAPI_ENDPOINT"].rstrip("/")
    mkt = os.environ.get("SPAPI_MARKETPLACE_ID","ATVPDKIKX0DER").strip()
    asin = os.environ["TEST_ASIN"].strip()
    cond = os.environ.get("ITEM_CONDITION","New").strip()

    access = lwa_token()
    url = f"{endpoint}/products/pricing/v0/items/{asin}/offers"
    params = {"MarketplaceId": mkt, "ItemCondition": cond}

    prepared = requests.Request("GET", url, params=params).prepare()

    base = {
        "host": prepared.url.split("/")[2],
        "x-amz-access-token": access,
        "content-type": "application/json",
    }
    headers = sign("GET", prepared.url, base)

    r = requests.get(prepared.url, headers=headers, timeout=30)
    r.raise_for_status()
    data = r.json()

    offers = (data.get("payload") or {}).get("Offers") or []
    if not offers:
        print("No offers returned.")
        return

    rows = []
    for o in offers:
        lp = money(o.get("ListingPrice"))
        ship = money(o.get("Shipping"))
        total = (lp or 0) + (ship or 0)
        rows.append({
            "total": total,
            "price": lp,
            "ship": ship,
            "sellerId": o.get("SellerId"),
            "fba": bool(o.get("IsFulfilledByAmazon")),
            "prime": bool((o.get("PrimeInformation") or {}).get("IsPrime")),
            "featured": bool(o.get("IsFeaturedMerchant")),
            "buybox": bool(o.get("IsBuyBoxWinner")),
            "pos": (o.get("SellerFeedbackRating") or {}).get("SellerPositiveFeedbackRating"),
            "count": (o.get("SellerFeedbackRating") or {}).get("FeedbackCount"),
        })

    # en ucuz toplam
    cheapest = min(rows, key=lambda x: x["total"])
    buybox = next((x for x in rows if x["buybox"]), None)

    print(f"ASIN: {asin} | Marketplace: {mkt} | Condition: {cond}")
    print(f"Offers: {len(rows)}")

    if buybox:
        print("\nBUYBOX:")
        print(buybox)

    print("\nCHEAPEST (price+ship):")
    print(cheapest)

    # ilk 5 en ucuz
    print("\nTOP 5 CHEAPEST:")
    for x in sorted(rows, key=lambda x: x["total"])[:5]:
        print(x)

