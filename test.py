

access_token = None
token_expiry = 0


import requests
import time
import json

# =============================
# CONFIG
# =============================



OAUTH_CLIENT_ID = '2f153525-799d-4983-9a10-dfc6a2f8f48c'
OAUTH_CLIENT_SECRET = 'HY8wCKHNxq9fviBfeLhtUE98PBew'
CLIENT_API_ID = '2f153525-799d-4983-9a10-dfc6a2f8f48c'

TOKEN_URL = "https://api.pverify.com/Token"
URL = "https://api.pverify.com/api/EligibilitySummary"
PAYER_URL = "https://api.pverify.com/API/GetAllPayers"


access_token = None
token_expiry = 0


# =============================
# GET ACCESS TOKEN
# =============================

def get_access_token():
    global access_token, token_expiry

    # Reuse token if still valid
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
        raise Exception(f"Token error: {response.text}")

    token_data = response.json()

    access_token = token_data["access_token"]
    token_expiry = time.time() + int(token_data["expires_in"]) - 60

    return access_token


# =============================
# GET ALL PAYERS
# =============================

def get_all_payers():

    token = get_access_token()

    headers = {
        "Authorization": f"Bearer {token}",
        "Client-API-Id": CLIENT_API_ID,
        "Content-Type": "application/json"
    }

    response = requests.get(PAYER_URL, headers=headers)

    if response.status_code != 200:
        raise Exception(f"GetAllPayers failed: {response.text}")

    return response.json()


# =============================
# UNIVERSAL PAYER PARSER
# =============================

def extract_payers(resp):

    # Case 1 → Response already list
    if isinstance(resp, list):
        return resp

    # Case 2 → Wrapped in "Payers"
    if isinstance(resp, dict) and "Payers" in resp:
        return resp["Payers"]

    # Case 3 → Wrapped in "data"
    if isinstance(resp, dict) and "data" in resp:
        return resp["data"]

    # Case 4 → Unknown structure → debug print
    print("\n⚠ Unknown response structure")
    print("Keys Found:", resp.keys())

    return []


# =============================
# MAIN
# =============================

if __name__ == "__main__":

    print("\nFetching Payers From pVerify...\n")

    raw_response = get_all_payers()

    # Debug raw structure once
    print("Top Level Response Type:", type(raw_response))

    if isinstance(raw_response, dict):
        print("Top Level Keys:", raw_response.keys())

    payer_list = extract_payers(raw_response)

    print(f"\nTotal Payers Returned: {len(payer_list)}\n")

    # Print first 20 sample payers
    for payer in payer_list[:20]:

        # Try multiple possible key formats
        payer_name = (
            payer.get("PayerName")
            or payer.get("payerName")
            or payer.get("Name")
        )

        payer_code = (
            payer.get("PayerCode")
            or payer.get("payerCode")
            or payer.get("Code")
        )

        print(f"{payer_name} → {payer_code}")

    # Optional → Save full list
    with open("payers_output.json", "w") as f:
        json.dump(payer_list, f, indent=2)

    print("\n✔ Full payer list saved to payers_output.json\n")
