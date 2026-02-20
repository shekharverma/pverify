from flask import Flask, render_template, request, jsonify
import requests
import json
import os
import urllib3
from datetime import datetime
from werkzeug.utils import secure_filename
from readpdf import extract_from_referral  # Import your extraction function

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Load .env from app directory
_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.isfile(_env_path):
    with open(_env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v

app = Flask(__name__)

# Config for Uploads
UPLOAD_FOLDER = 'uploads'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

ELIGIBILITY_API_URL = os.environ.get(
    "ELIGIBILITY_API_URL",
    "https://api.insuranceclaim.urtestsite.com/api/check-eligibility"
)

# ================= LOAD PAYERS =================
def load_payers():
    try:
        # This opens your JSON file containing the list of payers
        with open("payers_output.json", "r", encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading payers: {e}")
        return []

# ================= MAIN ROUTE (PRESERVED) =================
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
            # YOUR ORIGINAL STATIC DATA
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
            
            response = requests.post(ELIGIBILITY_API_URL, json=payload, verify=False)
            
            if response.status_code == 200:
                api_data = response.json()
                summary = api_data.get("PlanCoverageSummary") or {}
                dme = api_data.get("DMESummary") or {}
                oop = api_data.get("HBPC_Deductible_OOP_Summary") or {}
                services = api_data.get("ServiceDetails") or []

                plan_coins = (dme.get("CoInsInNet") or {}).get("Value")

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

                # CHANGED: Default values for copay and coins if not found
                spec_data = {"copay": "0", "coins": fmt_percent(plan_coins) or "0%", "auth": None, "desc": "Office Visit"}
                surg_data = {"coins": fmt_percent(plan_coins) or "0%", "auth": None, "desc": "Surgical Services"}

                for s in services:
                    name = s.get("ServiceName", "").lower()
                    details = s.get("EligibilityDetails") or []
                    for d in details:
                        benefit_type = d.get("EligibilityOrBenefit")
                        if "professional" in name or "office" in name:
                            if benefit_type == "Co-Payment":
                                val = d.get("MonetaryAmount")
                                if val is not None: spec_data["copay"] = val
                            if benefit_type == "Co-Insurance":
                                val = d.get("Percent")
                                if val is not None: spec_data["coins"] = fmt_percent(val)
                            auth = d.get("AuthorizationOrCertificationRequired")
                            if auth: spec_data["auth"] = auth
                        if "surgery" in name or "surgical" in name:
                            if benefit_type == "Co-Insurance":
                                val = d.get("Percent")
                                if val is not None: surg_data["coins"] = fmt_percent(val)
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

# ================= NEW UPLOAD ROUTE =================
@app.route('/upload-referral', methods=['POST'])
def upload_referral():
    if 'file' not in request.files:
        return jsonify({"error": "No file part"}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400
        
    filename = secure_filename(file.filename)
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)
    
    # Process with the logic
    data = extract_from_referral(filepath)
    
    if os.path.exists(filepath):
        os.remove(filepath)
    
    if data:
        return jsonify(data)
    
    return jsonify({"error": "Unknown system failure"}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8081)