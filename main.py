from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import requests
import time
from fastapi.middleware.cors import CORSMiddleware
import os
from dotenv import load_dotenv

load_dotenv() # Loads the .env file
app = FastAPI()

# ================= CONFIG =================
OAUTH_CLIENT_ID = os.environ.get('PVERIFY_OAUTH_CLIENT_ID')
OAUTH_CLIENT_SECRET = os.environ.get('PVERIFY_OAUTH_CLIENT_SECRET')
API_CLIENT_ID = os.environ.get('PVERIFY_API_CLIENT_ID')

TOKEN_URL = os.environ.get('PVERIFY_TOKEN_URL')
SUMMARY_URL = os.environ.get('PVERIFY_SUMMARY_URL')
access_token = None
token_expiry = 0

origins = [
    "https://insuranceclaim.urtestsite.com",  # Your Flask frontend domain
    "http://localhost:5001",                # Local Flask (usually 5000)
    "http://127.0.0.1:5001/",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,             # Allows specific domains
    allow_credentials=True,
    allow_methods=["*"],               # Allows all methods (GET, POST, etc.)
    allow_headers=["*"],               # Allows all headers
)
# ================= TOKEN =================
def get_access_token():
    global access_token, token_expiry

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
        raise Exception(response.text)

    token_data = response.json()

    access_token = token_data["access_token"]
    token_expiry = time.time() + int(token_data["expires_in"]) - 60

    return access_token


# ================= ELIGIBILITY SUMMARY =================
@app.post("/api/check-eligibility")
async def check_eligibility(payload: dict):

    try:
        token = get_access_token()

        # Convert payload to EligibilitySummary format
        converted_payload = {
            "payerCode": payload["PayerCode"],

            "provider": {
                "firstName": "",
                "middleName": "",
                "lastName": payload["RequestingProvider"]["LastName"],
                "npi": payload["RequestingProvider"]["NPI"],
                "pin": ""
            },

            "subscriber": {
                "firstName": payload["Subscriber"]["FirstName"],
                "lastName": payload["Subscriber"]["LastName"],
                "dob": payload["Subscriber"].get("DOB"),
                "memberID": payload["Subscriber"]["MemberID"]
            },

            "dependent": None,
            "isSubscriberPatient": str(payload["IsSubscriberPatient"]).lower(),

            "doS_StartDate": payload["DOS_StartDate"],
            "doS_EndDate": payload["DOS_EndDate"],

            "PracticeTypeCode": "3",
            "PlaceOfService": "11",
            "IncludeTextResponse": "false"
        }
        print('converted_payload',converted_payload)

        headers = {
            "Authorization": f"Bearer {token}",
            "Client-API-Id": API_CLIENT_ID,
            "Content-Type": "application/json"
        }

        response = requests.post(
            SUMMARY_URL,
            json=converted_payload,
            headers=headers
        )
        # print(response.json())
        return response.json()

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

