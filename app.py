from flask import Flask, render_template, request
import requests
import json
from datetime import datetime

app = Flask(__name__)

# ================= LOAD PAYERS =================
def load_payers():
    try:
        with open("payers_output.json", "r", encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading payers: {e}")
        return []

# ================= MERGED ROUTE =================
@app.route('/', methods=['GET', 'POST'])
def index():
    payers = load_payers()
    formatted_data = None
    form_data = {} 

    if request.method == 'POST':
        form_data = request.form
        form_data_dict = form_data.to_dict()
        form_data_dict['payerDisplayInput'] = request.form.get('payerDisplayInput', '') 

        try:
            static_provider_last = "Corium Ventures Pllc"
            static_npi = "1346553120"
            static_dos = datetime.now().strftime("%m/%d/%Y") 
            static_is_sub_patient = True
            
            payload = {
                "PayerCode": request.form.get("payerCode"),
                "DOS_StartDate": static_dos,
                "DOS_EndDate": static_dos,
                "IsSubscriberPatient": static_is_sub_patient,
                "RequestingProvider": {
                    "ProviderType": "Billing",
                    "LastName": static_provider_last,
                    "NPI": static_npi
                },
                "Subscriber": {
                    "MemberID": request.form.get("memberId"),
                    "FirstName": request.form.get("firstName"),
                    "LastName": request.form.get("lastName"),
                    "DOB": request.form.get("dob")
                }
            }

            response = requests.post("http://127.0.0.1:8000/api/check-eligibility", json=payload)
            
            if response.status_code == 200:
                api_data = response.json()
                summary = api_data.get("PlanCoverageSummary") or {}
                dme = api_data.get("DMESummary") or {} 
                oop = api_data.get("HBPC_Deductible_OOP_Summary") or {}
                services = api_data.get("ServiceDetails") or []

                # Default values
                plan_coins = (dme.get("CoInsInNet") or {}).get("Value")
                
                # Helper to format percentage
                def fmt_percent(val):
                    if val is None: return None
                    s_val = str(val)
                    if s_val == "0": return "0%"
                    if "." in s_val:
                        try:
                            return f"{int(float(s_val)*100)}%"
                        except:
                            return s_val
                    return s_val

                # Initialize variables
                spec_data = {"copay": None, "coins": fmt_percent(plan_coins), "auth": None, "desc": "Office Visit"}
                surg_data = {"coins": fmt_percent(plan_coins), "auth": None, "desc": "Surgical Services"}

                for s in services:
                    name = s.get("ServiceName", "").lower()
                    details = s.get("EligibilityDetails") or []
                    for d in details:
                        benefit_type = d.get("EligibilityOrBenefit")
                        
                        # --- Specialist / Professional ---
                        if "professional" in name or "office" in name:
                            if benefit_type == "Co-Payment":
                                val = d.get("MonetaryAmount")
                                if val is not None: 
                                    spec_data["copay"] = val
                            
                            if benefit_type == "Co-Insurance":
                                val = d.get("Percent")
                                if val is not None:
                                    spec_data["coins"] = fmt_percent(val)

                            auth = d.get("AuthorizationOrCertificationRequired")
                            if auth: spec_data["auth"] = auth
                        
                        # --- Surgical ---
                        if "surgery" in name or "surgical" in name:
                            if benefit_type == "Co-Insurance":
                                val = d.get("Percent")
                                if val is not None:
                                    surg_data["coins"] = fmt_percent(val)
                            
                            auth = d.get("AuthorizationOrCertificationRequired")
                            if auth: surg_data["auth"] = auth

                formatted_data = {
                    "full_json": api_data,
                    "is_hmo": api_data.get("IsHMOPlan"), 
                    "status": summary.get("Status"),
                    "payer_name": api_data.get("PayerName", "United Healthcare"),
                    "ver_type": api_data.get("VerificationType", "Subscriber Verification"),
                    "dos": api_data.get("DOS"),
                    "effective": summary.get("EffectiveDate"),
                    "expiry": summary.get("ExpiryDate"),
                    "plan_name": summary.get("PlanName"),
                    "policy_type": summary.get("PolicyType"),
                    "group_num": summary.get("GroupNumber"),
                    "group_name": summary.get("GroupName"),
                    "gender": summary.get("PatientGender"),
                    "benefits": {
                        "specialist": spec_data,
                        "surgical": surg_data,
                        # FORCE DEFAULT of "$0" if value is None
                        "oop": {
                            "indiv_deduct": (oop.get("IndividualDeductibleInNet") or {}).get("Value") or "$0",
                            "indiv_deduct_rem": (oop.get("IndividualDeductibleRemainingInNet") or {}).get("Value") or "$0",
                            "indiv_oop": (oop.get("IndividualOOP_InNet") or {}).get("Value") or "$0",
                            "indiv_oop_rem": (oop.get("IndividualOOPRemainingInNet") or {}).get("Value") or "$0",
                        }
                    }
                }
            else:
                formatted_data = {"error": f"API Error: {response.status_code} - {response.text}"}

        except Exception as e:
            formatted_data = {"error": f"System Error: {str(e)}"}
        
        return render_template("index.html", payers=payers, data=formatted_data, form_data=form_data_dict)

    return render_template("index.html", payers=payers, data=None, form_data={})

if __name__ == "__main__":
    app.run(port=5001, debug=True)