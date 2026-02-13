import os
import requests
from dotenv import load_dotenv

from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.credentials import Credentials

load_dotenv(dotenv_path=".env")
LWA_TOKEN_URL = "https://api.amazon.com/auth/o2/token"

def get_lwa_access_token():
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
    if r.status_code != 200:
        print("LWA TOKEN ERROR:", r.status_code, r.text)
    r.raise_for_status()
    return r.json()["access_token"]

def sign_v4(method: str, url: str, headers: dict):
    creds = Credentials(os.environ["AWS_ACCESS_KEY_ID"], os.environ["AWS_SECRET_ACCESS_KEY"])
    req = AWSRequest(method=method, url=url, data=None, headers=headers)
    SigV4Auth(creds, "execute-api", os.environ.get("AWS_REGION", "us-east-1")).add_auth(req)
    return dict(req.headers)

def main():
    endpoint = os.environ["SPAPI_ENDPOINT"].rstrip("/")
    marketplace_id = os.environ.get("SPAPI_MARKETPLACE_ID", "ATVPDKIKX0DER").strip()
    asin = os.environ.get("TEST_ASIN", "").strip()

    if not asin:
        raise SystemExit("TEST_ASIN missing. Add TEST_ASIN=... to .env")

    access_token = get_lwa_access_token()

    path = "/products/pricing/v0/competitivePrice"
    url = endpoint + path

    params = {"MarketplaceId": marketplace_id, "ItemType": "Asin", "Asins": asin}

    # DEBUG: gerçek URL (query string dahil)
    prepared = requests.Request("GET", url, params=params).prepare()
    full_url = prepared.url
    print("DEBUG URL:", full_url)

    base_headers = {
        "host": prepared.url.split("/")[2],
        "x-amz-access-token": access_token,
        "content-type": "application/json",
    }

    signed_headers = sign_v4("GET", full_url, base_headers)

    r = requests.get(full_url, headers=signed_headers, timeout=30)
    print("STATUS:", r.status_code)
    print(r.text)
    try:
        j=r.json()
        if isinstance(j, dict) and "payload" in j:
            for it in j["payload"]:
                if isinstance(it, dict) and "errors" in it:
                    print("ITEM ERRORS:", it["errors"])
    except Exception:
        pass

if __name__ == "__main__":
    main()
