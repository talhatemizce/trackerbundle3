import os
import requests
from dotenv import load_dotenv

from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.credentials import Credentials

load_dotenv()

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
    # debug için:
    if r.status_code != 200:
        print("LWA TOKEN ERROR:", r.status_code, r.text)
    r.raise_for_status()
    return r.json()["access_token"]

def sigv4_headers(method: str, url: str, headers: dict, body: bytes | None = None):
    creds = Credentials(
        os.environ["AWS_ACCESS_KEY_ID"],
        os.environ["AWS_SECRET_ACCESS_KEY"]
    )
    req = AWSRequest(method=method, url=url, data=body, headers=headers)
    SigV4Auth(creds, "execute-api", os.environ.get("AWS_REGION", "us-east-1")).add_auth(req)
    return dict(req.headers)

def main():
    endpoint = os.environ.get("SPAPI_ENDPOINT", "").rstrip("/")
    if not endpoint:
        raise SystemExit("SPAPI_ENDPOINT missing in .env")

    access_token = get_lwa_access_token()

    path = "/sellers/v1/marketplaceParticipations"
    url = endpoint + path

    base_headers = {
        "host": url.split("/")[2],
        "x-amz-access-token": access_token,
        "content-type": "application/json",
    }

    headers = sigv4_headers("GET", url, base_headers)

    r = requests.get(url, headers=headers, timeout=30)
    print("STATUS:", r.status_code)
    print(r.text)

if __name__ == "__main__":
    main()
