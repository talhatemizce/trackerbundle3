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

def main():
    endpoint = os.environ["SPAPI_ENDPOINT"].rstrip("/")
    mkt = os.environ.get("SPAPI_MARKETPLACE_ID","ATVPDKIKX0DER").strip()
    asin = os.environ["TEST_ASIN"].strip()

    access = lwa_token()
    url = f"{endpoint}/products/pricing/v0/items/{asin}/offers"
    params = {"MarketplaceId": mkt, "ItemCondition": "New"}

    prepared = requests.Request("GET", url, params=params).prepare()
    print("DEBUG URL:", prepared.url)

    base = {
        "host": prepared.url.split("/")[2],
        "x-amz-access-token": access,
        "content-type": "application/json",
    }
    headers = sign("GET", prepared.url, base)

    r = requests.get(prepared.url, headers=headers, timeout=30)
    print("STATUS:", r.status_code)
    print(r.text)

if __name__ == "__main__":
    main()
