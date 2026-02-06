from fastapi import FastAPI, HTTPException
import requests
import os
import time
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

# OAuth credentials (for token generation)
OAUTH_CLIENT_ID = os.getenv("PVERIFY_CLIENT_ID")
OAUTH_CLIENT_SECRET = os.getenv("PVERIFY_CLIENT_SECRET")
TOKEN_URL = os.getenv("PVERIFY_TOKEN_URL")

# API credentials
API_CLIENT_ID = os.getenv("PVERIFY_API_CLIENT_ID")
BASE_URL = os.getenv("PVERIFY_BASE_URL")

# Token cache
access_token = None
token_expiry = 0


def get_access_token():
    global access_token, token_expiry

    # If token exists and not expired, reuse
    if access_token and time.time() < token_expiry:
        return access_token

    response = requests.post(
        TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "Client_Id": OAUTH_CLIENT_ID,
            "Client_Secret": OAUTH_CLIENT_SECRET,
            "grant_type": "client_credentials"
        }
    )

    if response.status_code != 200:
        raise Exception(f"Token generation failed: {response.text}")

    token_data = response.json()

    access_token = token_data["access_token"]
    expires_in = int(token_data["expires_in"])

    # Save expiry timestamp
    token_expiry = time.time() + expires_in - 60  # refresh 1 min early

    return access_token


@app.post("/api/check-eligibility")
async def check_eligibility(payload: dict):

    try:
        token = get_access_token()

        url = f"{BASE_URL}/EligibilityInquiry"

        headers = {
            "Authorization": f"Bearer {token}",
            "Client-API-Id": API_CLIENT_ID,
            "Content-Type": "application/json"
        }

        response = requests.post(
            url,
            json=payload,
            headers=headers,
            timeout=30
        )

        return response.json()

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
