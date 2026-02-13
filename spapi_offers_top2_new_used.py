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
    if not x: return 0.0
    return float(x.get("Amount", 0.0) or 0.0)

def fetch_offers(endpoint, asin, marketplace_id, condition, access_token):
    url = f"{endpoint}/products/pricing/v0/items/{asin}/offers"
    params = {"MarketplaceId": marketplace_id, "ItemCondition": condition}
    prepared = requests.Request("GET", url, params=params).prepare()

    base = {
        "host": prepared.url.split("/")[2],
        "x-amz-access-token": access_token,
        "content-type": "application/json",
    }
    headers = sign("GET", prepared.url, base)

    r = requests.get(prepared.url, headers=headers, timeout=30)
    r.raise_for_status()
    payload = (r.json() or {}).get("payload") or {}
    offers = payload.get("Offers") or []

    rows = []
    for o in offers:
        lp = money(o.get("ListingPrice"))
        ship = money(o.get("Shipping"))
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

    rows_sorted = sorted(rows, key=lambda x: x["total"])
    buybox = next((x for x in rows_sorted if x["buybox"]), None)
    top2 = rows_sorted[:2]
    return buybox, top2, len(rows_sorted)

def main():
    endpoint = os.environ["SPAPI_ENDPOINT"].rstrip("/")
    mkt = os.environ.get("SPAPI_MARKETPLACE_ID","ATVPDKIKX0DER").strip()
    asin = os.environ["TEST_ASIN"].strip()

    access = lwa_token()

    print(f"ASIN: {asin} | Marketplace: {mkt}\n")

    for cond in ["New", "Used"]:
        try:
            buybox, top2, n = fetch_offers(endpoint, asin, mkt, cond, access)
        except requests.HTTPError as e:
            print(f"== {cond} ==")
            r = e.response
            print("HTTP:", r.status_code)
            print(r.text)
            print()
            continue

        print(f"== {cond} ==")
        print("Offers:", n)

        if buybox:
            print("BUYBOX:", buybox)
        else:
            print("BUYBOX: (none returned)")

        if top2:
            print("TOP 2 CHEAPEST:")
            for x in top2:
                print(x)
        else:
            print("TOP 2 CHEAPEST: (no offers)")
        print()

if __name__ == "__main__":
    main()
