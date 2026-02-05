from fastapi import FastAPI, HTTPException
import requests
import os
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

PVERIFY_API_TOKEN = os.getenv("PVERIFY_API_TOKEN")
PVERIFY_CLIENT_ID = os.getenv("PVERIFY_CLIENT_ID")
PVERIFY_BASE_URL = os.getenv("PVERIFY_BASE_URL")

@app.post("/api/check-eligibility")
async def check_eligibility(payload: dict):
    url = f"{PVERIFY_BASE_URL}/EligibilityInquiry"
    headers = {
        "Authorization": f"Bearer {PVERIFY_API_TOKEN}",
        "Client-API-Id": PVERIFY_CLIENT_ID,
        "Content-Type": "application/json"
    }

    try:
        response = requests.post(
            url,
            json=payload,
            headers=headers,
            timeout=30
        )

        return {
            "success": True,
            "pverify_response": response.json()
        }

    except requests.exceptions.RequestException as e:
        print("str e ", str(e))
        raise HTTPException(
            status_code=500,
            detail=f"pVerify API error: {str(e)}"
        )
