from fastapi import FastAPI, HTTPException
import requests
import time

app = FastAPI()

# ================= CONFIG =================
OAUTH_CLIENT_ID = '2f153525-799d-4983-9a10-dfc6a2f8f48c'
OAUTH_CLIENT_SECRET = 'HY8wCKHNxq9fviBfeLhtUE98PBew'
API_CLIENT_ID = '2f153525-799d-4983-9a10-dfc6a2f8f48c'

TOKEN_URL = 'https://api.pverify.com/Token'
SUMMARY_URL = 'https://api.pverify.com/api/EligibilitySummary'

access_token = None
token_expiry = 0


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
        print(response.json())
        return response.json()

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
