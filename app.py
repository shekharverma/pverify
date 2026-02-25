import logging
import sys
import requests
import json
import os
import urllib3
import time
import concurrent.futures
from flask import Flask, render_template, request, jsonify, session, redirect, url_for, send_from_directory
from datetime import datetime
from werkzeug.utils import secure_filename
from readpdf import extract_from_referral
from models import db, User, PayerMapping, Patient 
from flask_migrate import Migrate
from sqlalchemy import func

# --- LOGGING SETUP ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("MedBillApp")
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- CONFIGURATION ---
app = Flask(__name__)
app.secret_key = "prod_secret_key"
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get(
    "DATABASE_URL", 
    "mysql+mysqlconnector://dev:Admin%401234@10.91.0.128/pverifyDB"
)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = 'uploads'

db.init_app(app)
migrate = Migrate(app, db)
os.makedirs('uploads', exist_ok=True)

# --- PVERIFY CREDENTIALS ---
PVERIFY_CLIENT_ID = os.environ.get('PVERIFY_OAUTH_CLIENT_ID')
PVERIFY_CLIENT_SECRET = os.environ.get('PVERIFY_OAUTH_CLIENT_SECRET')
PVERIFY_API_CLIENT_ID = os.environ.get('PVERIFY_API_CLIENT_ID')
PVERIFY_TOKEN_URL = "https://api.pverify.com/Token"
PVERIFY_SUMMARY_URL = "https://api.pverify.com/API/EligibilitySummary"

_access_token, _token_expiry = None, 0

# --- HELPER FUNCTIONS ---

def load_payers():
    try:
        with open("payers_output.json", "r", encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error loading payers.json: {e}")
        return []

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in {'pdf', 'png', 'jpg', 'jpeg', 'webp'}

def get_pverify_token():
    global _access_token, _token_expiry
    if _access_token and time.time() < _token_expiry:
        return _access_token
    try:
        res = requests.post(
            PVERIFY_TOKEN_URL, 
            data={
                "Client_Id": PVERIFY_CLIENT_ID, 
                "Client_Secret": PVERIFY_CLIENT_SECRET, 
                "grant_type": "client_credentials"
            }
        )
        if res.status_code == 200:
            data = res.json()
            _access_token = data["access_token"]
            _token_expiry = time.time() + int(data["expires_in"]) - 60
            return _access_token
    except Exception as e:
        logger.error(f"Token Error: {e}")
    return None

def get_payer_code_by_name(name_to_find):
    if not name_to_find: return None
    name_clean = name_to_find.strip().lower()
    
    # 1. Check DB Mappings
    mapping = PayerMapping.query.filter(func.lower(PayerMapping.pdf_payer_name) == name_clean).first()
    if mapping:
        parts = mapping.system_payer_name.split(' - ')
        return parts[-1].strip() if len(parts) > 1 else None
    
    # 2. Check JSON File directly
    all_payers = load_payers()
    for p in all_payers:
        if p.get('payerName', '').strip().lower() == name_clean:
            return p.get('payerCode')
            
    return None 

def perform_verification(payer_code, member_id, first, last, dob):
    token = get_pverify_token()
    if not token: return {"success": False, "error": "Auth Failed"}
    
    try:
        payload = {
            "payerCode": payer_code,
            "provider": {"lastName": "Corium Ventures Pllc", "npi": "1770098261"}, 
            "subscriber": {"firstName": first, "lastName": last, "dob": dob, "memberID": str(member_id).strip()},
            "isSubscriberPatient": "true", 
            "doS_StartDate": datetime.now().strftime("%m/%d/%Y"),
            "doS_EndDate": datetime.now().strftime("%m/%d/%Y"), 
            "PracticeTypeCode": "3", 
            "PlaceOfService": "11"
        }
        
        headers = {"Authorization": f"Bearer {token}", "Client-API-Id": PVERIFY_API_CLIENT_ID, "Content-Type": "application/json"}
        response = requests.post(PVERIFY_SUMMARY_URL, json=payload, headers=headers)
        
        if response.status_code == 200:
            api_data = response.json()
            
            # Error Check
            if api_data.get("APIResponseCode") != "0":
                return {"success": False, "error": api_data.get("APIResponseMessage"), "raw_response": api_data}

            # 1. Plan Type Logic
            plan_summary = api_data.get("PlanCoverageSummary") or {}
            p_type = plan_summary.get("PolicyType") or ""
            p_name = (plan_summary.get("PlanName") or "").upper()
            
            display_plan = p_type
            if "PLAN G" in p_name: display_plan = "Plan G"
            elif "PLAN N" in p_name: display_plan = "Plan N"
            elif "MEDICARE ADVANTAGE" in p_name: display_plan = f"MA {p_type}".strip()

            # 2. Financials (STRICT SPECIALIST FIX)
            copay, coins = "$0.00", "0%"
            service_details = api_data.get("ServiceDetails") or []
            
            for service in service_details:
                # CRITICAL FIX: Only look for "Professional (Physician)"
                # Do NOT include "Visit - Office" or it will grab the $5.00 PCP rate
                if service.get("ServiceName") == "Professional (Physician)":
                    for detail in service.get("EligibilityDetails") or []:
                        if detail.get("EligibilityOrBenefit") == "Co-Payment":
                            val = detail.get("MonetaryAmount")
                            if val: copay = f"${float(val):.2f}"
                        if detail.get("EligibilityOrBenefit") == "Co-Insurance":
                            val = detail.get("Percent")
                            if val is not None: coins = f"{int(float(val)*100)}%"

            # 3. Deductible/OOP
            oop_summary = api_data.get("HBPC_Deductible_OOP_Summary") or {}
            ded_rem = (oop_summary.get("IndividualDeductibleRemainingInNet") or {}).get("Value") or "$0.00"
            oop_rem = (oop_summary.get("IndividualOOPRemainingInNet") or {}).get("Value") or "$0.00"

            return {
                "success": True, 
                "payer_name": api_data.get("PayerName"),
                "plan_type": display_plan,
                "benefits": {"copay": copay, "coins": coins, "deductible": ded_rem, "oop": oop_rem},
                "raw_response": api_data
            }
        
        return {"success": False, "error": f"API Error {response.status_code}"}
    except Exception as e: return {"success": False, "error": str(e)}

def process_single_file(filepath, app_context_app):
    with app_context_app.app_context():
        try:
            extraction = extract_from_referral(filepath)
            root = extraction.get("form_population_data", {})
            p_data = root.get("primary", {})
            s_data = root.get("secondary", {})
            
            patient = Patient(
                first_name=root.get("first_name"), last_name=root.get("last_name"), dob=root.get("dob"),
                member_id=p_data.get("member_id"), payer_name=p_data.get("payer_name"),
                sec_member_id=s_data.get("member_id"), sec_payer_name=s_data.get("payer_name")
            )

            # Verification Logic
            p_code = get_payer_code_by_name(patient.payer_name)
            raw_json = {}
            
            if p_code:
                res = perform_verification(p_code, patient.member_id, patient.first_name, patient.last_name, patient.dob)
                if res["success"]:
                    patient.status = "verified"
                    patient.plan_type = res["plan_type"]
                    patient.copay, patient.coins = res["benefits"]["copay"], res["benefits"]["coins"]
                    patient.deductible_rem, patient.oop_rem = res["benefits"]["deductible"], res["benefits"]["oop"]
                    raw_json = res.get("raw_response")
                else: 
                    patient.status = "error"
                    raw_json = res.get("raw_response")
            else: 
                patient.status = "mapping_needed"

            # Secondary Trigger
            if "medicare" in (patient.payer_name or "").lower() and patient.sec_member_id:
                s_code = get_payer_code_by_name(patient.sec_payer_name)
                if s_code:
                    res_s = perform_verification(s_code, patient.sec_member_id, patient.first_name, patient.last_name, patient.dob)
                    if res_s["success"]:
                        if res_s["plan_type"]:
                            patient.plan_type = f"{patient.plan_type} + {res_s['plan_type']}" if patient.plan_type else res_s['plan_type']
                        patient.copay, patient.coins = res_s["benefits"]["copay"], res_s["benefits"]["coins"]
                        patient.deductible_rem, patient.oop_rem = res_s["benefits"]["deductible"], res_s["benefits"]["oop"]
                        raw_json = res_s.get("raw_response")

            db.session.add(patient)
            db.session.commit()
            
            # Return extraction data so Gemini Icon works
            return {"id": patient.id, "pverify_raw": raw_json, "gemini_raw": extraction}
        
        finally:
            # DO NOT DELETE FILE so we can serve it to the frontend
            # if os.path.exists(filepath): os.remove(filepath)
            pass

# --- ROUTES ---

@app.route('/uploads/<path:filename>')
def serve_uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/upload-single', methods=['POST'])
def upload_single():
    f = request.files.get('file')
    if f:
        # Unique name prevents race conditions
        unique_name = f"{time.time()}_{secure_filename(f.filename)}"
        path = os.path.join(app.config['UPLOAD_FOLDER'], unique_name)
        f.save(path)
        
        res = process_single_file(path, app)
        p = Patient.query.get(res["id"])
        
        return jsonify({"success":True, "patient": {
            "id": p.id, "first_name": p.first_name, "last_name": p.last_name, "dob": p.dob,
            "payer_name": p.payer_name, "plan_type": p.plan_type, "status": p.status,
            "copay": p.copay, "coins": p.coins, "deductible_rem": p.deductible_rem, "oop_rem": p.oop_rem,
            "pverify_raw": res.get("pverify_raw"),
            "gemini_raw": res.get("gemini_raw"), # Passed for Robot Icon
            "filename": unique_name              # Passed for Document Icon
        }})
    return jsonify({"success": False, "error": "No file"})

@app.route('/batch-upload', methods=['POST'])
def batch_upload():
    if 'files' not in request.files: return jsonify({"error": "No files"}), 400
    files = request.files.getlist('files')
    saved_paths = []
    
    for file in files:
        if file and allowed_file(file.filename):
            unique_name = f"{datetime.now().timestamp()}_{secure_filename(file.filename)}"
            path = os.path.join(app.config['UPLOAD_FOLDER'], unique_name)
            file.save(path)
            saved_paths.append(path)
    
    if not saved_paths: return jsonify({"error": "No valid files"}), 400

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(process_single_file, path, app) for path in saved_paths]
        concurrent.futures.wait(futures)
            
    return jsonify({"success": True})

@app.route('/')
def index():
    if not session.get('role'): return render_template("index.html")
    patients = Patient.query.order_by(Patient.created_at.desc()).all()
    # load_payers() is now defined so this won't crash
    return render_template("index.html", payers=load_payers(), patients=patients)

@app.route('/login', methods=['POST'])
def login():
    code = request.form.get('access_code')
    user = User.query.filter_by(access_code=code).first()
    if user:
        session['role'], session['name'] = user.role, user.name
        return redirect(url_for('index'))
    return render_template("index.html", payers=load_payers(), login_error="Invalid Access Code")

@app.route('/manual-add', methods=['POST'])
def manual_add():
    if not session.get('role'): return jsonify({"error": "Unauthorized"}), 403
    form = request.form
    payer_code = form.get("payerCode")
    verify_result = perform_verification(payer_code, form.get("memberId"), form.get("firstName"), form.get("lastName"), form.get("dob"))
    new_patient = Patient(first_name=form.get("firstName"), last_name=form.get("lastName"), dob=form.get("dob"), member_id=form.get("memberId"), payer_name=form.get("payerDisplayInput"))
    if verify_result["success"]:
        new_patient.status, new_patient.plan_type = "verified", verify_result["plan_type"]
        new_patient.copay, new_patient.coins = verify_result["benefits"]["copay"], verify_result["benefits"]["coins"]
        new_patient.deductible_rem, new_patient.oop_rem = verify_result["benefits"]["deductible"], verify_result["benefits"]["oop"]
    else: new_patient.status = "error"
    db.session.add(new_patient); db.session.commit()
    return redirect(url_for('index'))

@app.route('/delete-patient/<int:id>', methods=['POST'])
def delete_patient(id):
    p = Patient.query.get(id)
    if p: db.session.delete(p); db.session.commit(); return jsonify({"success": True})
    return jsonify({"error": "Not found"}), 404

@app.route('/delete-all-patients', methods=['POST'])
def delete_all_patients():
    db.session.query(Patient).delete(); db.session.commit(); return jsonify({"success": True})

@app.route('/api/mappings', methods=['GET', 'POST', 'DELETE'])
def handle_mappings():
    if request.method == 'GET':
        return jsonify({m.pdf_payer_name: m.system_payer_name for m in PayerMapping.query.all()})
    data = request.json
    pdf_name = data.get('pdf_name', '').strip().lower()
    if request.method == 'POST':
        mapping = PayerMapping.query.filter_by(pdf_payer_name=pdf_name).first()
        if mapping: mapping.system_payer_name = data.get('system_name')
        else: db.session.add(PayerMapping(pdf_payer_name=pdf_name, system_payer_name=data.get('system_name')))
    if request.method == 'DELETE':
        mapping = PayerMapping.query.filter_by(pdf_payer_name=pdf_name).first()
        if mapping: db.session.delete(mapping)
    db.session.commit(); return jsonify({"success": True})

@app.route('/change-code', methods=['POST'])
def change_code():
    if not session.get('role'): return jsonify({"success": False, "error": "Not authenticated"})
    user = User.query.filter_by(access_code=request.form.get('current_code')).first()
    if not user: return jsonify({"success": False, "error": "Incorrect PIN"})
    if User.query.filter_by(access_code=request.form.get('new_code')).first(): return jsonify({"success": False, "error": "PIN in use"})
    user.access_code = request.form.get('new_code'); db.session.commit()
    return jsonify({"success": True})

@app.route('/logout')
def logout():
    session.clear(); return redirect(url_for('index'))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8081)