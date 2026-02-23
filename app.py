from flask import Flask, render_template, request, jsonify, session, redirect, url_for
import requests
import json
import os
import urllib3
from datetime import datetime
from werkzeug.utils import secure_filename
from readpdf import extract_from_referral

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

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
app.secret_key = "super_secret_healthcare_key_change_in_prod" 

# Config for Uploads
UPLOAD_FOLDER = 'uploads'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

ELIGIBILITY_API_URL = os.environ.get(
    "ELIGIBILITY_API_URL",
    "https://api.insuranceclaim.urtestsite.com/api/check-eligibility"
)

# ================= ROLE BASED LOGIN DB =================
import json
import os

# Define the path for the persistent user database
USERS_FILE = "users.json"

# Helper to load users from file or use defaults if file doesn't exist
def load_users():
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE, "r") as f:
            return json.load(f)
    else:
        # Initial default data
        defaults = {
            "7432": {"role": "admin", "name": "Dr. Amit"},
            "2262": {"role": "provider", "name": "Provider/PA"},
            "1234": {"role": "checkout", "name": "Checkout Desk"}
        }
        save_users(defaults)
        return defaults

# Helper to save users to the physical file
def save_users(users_dict):
    with open(USERS_FILE, "w") as f:
        json.dump(users_dict, f, indent=4)

# Load users at startup
USERS = load_users()

@app.route('/login', methods=['POST'])
def login():
    # RE-LOAD users from file to ensure we have the latest changed PINs
    global USERS
    USERS = load_users()
    
    access_code = request.form.get('access_code')
    user = USERS.get(access_code)
    if user:
        session['role'] = user['role']
        session['name'] = user['name']
        session['current_code'] = access_code
        return redirect(url_for('index'))
    return render_template("index.html", payers=load_payers(), login_error="Invalid Access Code")

@app.route('/change-code', methods=['POST'])
def change_code():
    if not session.get('role'):
        return jsonify({"success": False, "error": "Not authenticated."})
    
    # RE-LOAD users to ensure we aren't overwriting someone else's change
    users_db = load_users()
    
    current_code = request.form.get('current_code')
    new_code = request.form.get('new_code')
    
    if current_code != session.get('current_code') or current_code not in users_db:
        return jsonify({"success": False, "error": "Incorrect current PIN."})
    
    if new_code in users_db:
        return jsonify({"success": False, "error": "PIN already in use. Choose another."})
        
    # Move the user data to the new key and remove the old one
    user_data = users_db.pop(current_code)
    users_db[new_code] = user_data
    
    # PERSIST TO FILE
    save_users(users_db)
    
    # Update local memory and session
    session['current_code'] = new_code 
    
    return jsonify({"success": True, "message": "PIN updated and saved permanently!"})

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

def load_payers():
    try:
        with open("payers_output.json", "r", encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading payers: {e}")
        return []

# ================= MAIN ROUTE =================
@app.route('/', methods=['GET', 'POST'])
def index():
    payers = load_payers()
    formatted_data = None
    form_data = {}

    if request.method == 'POST' and 'payerCode' in request.form:
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
                    if s_val == "0": return "0.00%"
                    if "." in s_val:
                        try:
                            return f"{float(s_val)*100:.0f}%" # Rounded percent like screenshot
                        except:
                            return s_val
                    return s_val

                spec_data = {"copay": "0.00", "coins": fmt_percent(plan_coins) or "0.00%", "auth": None, "desc": "Office Visit"}
                surg_data = {"coins": fmt_percent(plan_coins) or "0.00%", "auth": None, "desc": "Surgical Services"}

                for s in services:
                    name = s.get("ServiceName", "").lower()
                    details = s.get("EligibilityDetails") or []
                    for d in details:
                        benefit_type = d.get("EligibilityOrBenefit")
                        if "professional" in name or "office" in name:
                            if benefit_type == "Co-Payment":
                                val = d.get("MonetaryAmount")
                                if val is not None: 
                                    try:
                                        spec_data["copay"] = f"{float(val):.2f}"
                                    except (ValueError, TypeError):
                                        spec_data["copay"] = str(val)
                            
                            if benefit_type == "Co-Insurance":
                                val = d.get("Percent")
                                if val is not None: spec_data["coins"] = fmt_percent(val)
                            auth = d.get("AuthorizationOrCertificationRequired")
                            if auth: spec_data["auth"] = auth

                formatted_data = {
                    "full_json": api_data,
                    "is_hmo": api_data.get("IsHMOPlan"),
                    "status": summary.get("Status"),
                    "payer_name": api_data.get("PayerName", "Unknown Payer"),
                    "ver_type": api_data.get("VerificationType", "Subscriber Verification"),
                    "dos": api_data.get("DOS"),
                    "benefits": {
                        "specialist": spec_data,
                        "surgical": surg_data,
                        "oop": {
                            # RESTORED ALL VARIABLES
                            "indiv_deduct_rem": (oop.get("IndividualDeductibleRemainingInNet") or {}).get("Value") or "$0.00",
                            "indiv_oop_rem": (oop.get("IndividualOOPRemainingInNet") or {}).get("Value") or "$0.00"
                        }
                    }
                }
            else:
                formatted_data = {"error": f"API Error: {response.status_code} - {response.text}"}
        except Exception as e:
            formatted_data = {"error": f"System Error: {str(e)}"}

        return render_template("index.html", payers=payers, data=formatted_data, form_data=form_data_dict)

    return render_template("index.html", payers=payers, data=None, form_data={})

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
    
    data = extract_from_referral(filepath)
    if os.path.exists(filepath):
        os.remove(filepath)
    
    if data:
        return jsonify(data)
    
    return jsonify({"error": "Unknown system failure"}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8081)