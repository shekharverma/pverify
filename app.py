from flask import Flask, render_template, request
import requests

app = Flask(__name__)

# This is your actual response data structure for testing
STATIC_RESPONSE = {
    "status": "Active",
    "payerName": "Blue Cross Blue Shield Texas",
    "plan": "BLUECHOICE PPO - PPO",
    "dos": "02/04/2026",
    "eligibilityPeriod": {"effectiveFromDate": "11/01/2024"},
    "demographicInfo": {
        "subscriber": {
            "fullName": "JESUS NUNEZ",
            "doB_R": "09/10/1988",
            "identification": [
                {"code": "NUNEZ", "type": "Lastname_R"},
                {"code": "JESUS", "type": "Firstname"},
                {"code": "ZGP814929806", "type": "Member ID"},
                {"code": "251724", "type": "Group Number"}
            ]
        }
    },
    "networkSections": [
        {"label": "Primary Care", "inNetworkParameters": [{"key": "Co-Ins", "value": "0"}]},
        {"label": "Out-Of-Pocket", "inNetworkParameters": [{"key": "Remaining", "value": "6670.78"}]},
        {"label": "Specialist", "inNetworkParameters": [{"key": "Co-Ins", "value": "0"}, {"key": "Co-Pay", "value": "90.00"}]}
    ],
    "servicesTypes": [
        {
            "serviceTypeName": "Chiropractic",
            "serviceTypeSections": [{
                "label": "In Plan-Network Status",
                "serviceParameters": [
                    {"key": "Individual Calendar Year Co-Insurance", "value": "20%", "message": ["MUSCLE MANIPULATION"]},
                    {"key": "Individual Remaining Deductible", "value": "$1750.00"}
                ]
            }]
        },
        {
            "serviceTypeName": "Urgent Care",
            "serviceTypeSections": [{
                "label": "In Plan-Network Status",
                "serviceParameters": [
                    {"key": "Individual Day Co-Payment", "value": "$90.00", "message": ["EMERGENCY OFFICE VISIT"]}
                ]
            }]
        }
    ]
}

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/verify', methods=['POST'])
def verify():
    # TEST MODE: Returning static data
    # TO ENABLE LIVE API: 
    # response = requests.post("http://127.0.0.1:8000/api/check-eligibility", json=request.form)
    # data = response.json()["pverify_response"]
    
    return render_template('report.html', report=STATIC_RESPONSE)

if __name__ == '__main__':
    app.run(port=5000, debug=True)