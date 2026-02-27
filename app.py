import logging
import sys
import requests
import json
import os
import urllib3
import time
import concurrent.futures
import csv
import io
import re
from flask import Flask, render_template, request, jsonify, session, redirect, url_for, send_from_directory
from datetime import datetime
from werkzeug.utils import secure_filename
from readpdf import extract_from_referral
from models import db, User, PayerMapping, Patient, Location, MedicalCode, Pricing
from flask_migrate import Migrate
from sqlalchemy import func

ENABLE_MEDICARE_SECONDARY_CHECK = False

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', handlers=[logging.StreamHandler(sys.stdout)])
logger = logging.getLogger("MedBillApp")
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)
app.secret_key = "prod_secret_key"
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get("DATABASE_URL", "mysql+mysqlconnector://dev:Admin%401234@10.91.0.128/pverifyDB")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = 'uploads'

db.init_app(app)
migrate = Migrate(app, db)
os.makedirs('uploads', exist_ok=True)

PVERIFY_CLIENT_ID = os.environ.get('PVERIFY_OAUTH_CLIENT_ID')
PVERIFY_CLIENT_SECRET = os.environ.get('PVERIFY_OAUTH_CLIENT_SECRET')
PVERIFY_API_CLIENT_ID = os.environ.get('PVERIFY_API_CLIENT_ID')
PVERIFY_TOKEN_URL = "https://api.pverify.com/Token"
PVERIFY_SUMMARY_URL = "https://api.pverify.com/API/EligibilitySummary"
_access_token, _token_expiry = None, 0

def load_payers():
    try:
        with open("payers_output.json", "r", encoding='utf-8') as f: return json.load(f)
    except: return []

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in {'pdf', 'png', 'jpg', 'jpeg', 'webp', 'csv'}

def get_pverify_token():
    global _access_token, _token_expiry
    if _access_token and time.time() < _token_expiry: return _access_token
    try:
        res = requests.post(PVERIFY_TOKEN_URL, data={"Client_Id": PVERIFY_CLIENT_ID, "Client_Secret": PVERIFY_CLIENT_SECRET, "grant_type": "client_credentials"})
        if res.status_code == 200:
            data = res.json(); _access_token = data["access_token"]; _token_expiry = time.time() + int(data["expires_in"]) - 60; return _access_token
    except: pass
    return None

def get_payer_code_by_name(name_to_find):
    if not name_to_find: return None
    name_clean = name_to_find.strip().lower()
    mapping = PayerMapping.query.filter(func.lower(PayerMapping.pdf_payer_name) == name_clean).first()
    if mapping:
        parts = mapping.system_payer_name.split(' - ')
        return parts[-1].strip() if len(parts) > 1 else None
    for p in load_payers():
        if p.get('payerName', '').strip().lower() == name_clean: return p.get('payerCode')
    return None 

def perform_verification(payer_code, member_id, first, last, dob):
    token = get_pverify_token()
    if not token: return {"success": False, "error": "Auth Failed"}
    try:
        payload = {"payerCode": payer_code, "provider": {"lastName": "Corium Ventures Pllc", "npi": "1770098261"}, "subscriber": {"firstName": first, "lastName": last, "dob": dob, "memberID": str(member_id).strip()}, "isSubscriberPatient": "true", "doS_StartDate": datetime.now().strftime("%m/%d/%Y"), "doS_EndDate": datetime.now().strftime("%m/%d/%Y"), "PracticeTypeCode": "3", "PlaceOfService": "11"}
        headers = {"Authorization": f"Bearer {token}", "Client-API-Id": PVERIFY_API_CLIENT_ID, "Content-Type": "application/json"}
        response = requests.post(PVERIFY_SUMMARY_URL, json=payload, headers=headers)
        if response.status_code == 200:
            api_data = response.json()
            if api_data.get("APIResponseCode") != "0": return {"success": False, "error": api_data.get("APIResponseMessage"), "raw_response": api_data}
            plan_summary = api_data.get("PlanCoverageSummary") or {}
            p_type, p_name = plan_summary.get("PolicyType") or "", (plan_summary.get("PlanName") or "").upper()
            display_plan = "Plan G" if "PLAN G" in p_name else "Plan N" if "PLAN N" in p_name else f"MA {p_type}".strip() if "MEDICARE ADVANTAGE" in p_name else p_type
            copay, coins = "$0.00", "0%"
            for service in api_data.get("ServiceDetails") or []:
                if service.get("ServiceName") == "Professional (Physician)":
                    for detail in service.get("EligibilityDetails") or []:
                        if detail.get("EligibilityOrBenefit") == "Co-Payment" and detail.get("MonetaryAmount"): copay = f"${float(detail.get('MonetaryAmount')):.2f}"
                        if detail.get("EligibilityOrBenefit") == "Co-Insurance" and detail.get("Percent") is not None: coins = f"{int(float(detail.get('Percent'))*100)}%"
            oop_summary = api_data.get("HBPC_Deductible_OOP_Summary") or {}
            ded_rem = (oop_summary.get("IndividualDeductibleRemainingInNet") or {}).get("Value") or "$0.00"
            oop_rem = (oop_summary.get("IndividualOOPRemainingInNet") or {}).get("Value") or "$0.00"
            return {"success": True, "payer_name": api_data.get("PayerName"), "plan_type": display_plan, "benefits": {"copay": copay, "coins": coins, "deductible": ded_rem, "oop": oop_rem}, "raw_response": api_data}
        return {"success": False, "error": f"API Error {response.status_code}"}
    except Exception as e: return {"success": False, "error": str(e)}

def process_single_file(filepath, app_context_app, filename=None):
    with app_context_app.app_context():
        try:
            extraction = extract_from_referral(filepath)
            root = extraction.get("form_population_data", {})
            p_data, s_data = root.get("primary", {}), root.get("secondary", {})
            patient = Patient(first_name=root.get("first_name"), last_name=root.get("last_name"), dob=root.get("dob"), member_id=p_data.get("member_id"), payer_name=p_data.get("payer_name"), sec_member_id=s_data.get("member_id"), sec_payer_name=s_data.get("payer_name"))
            
            if filename: patient.file_path = filename
            patient.gemini_raw = json.dumps(extraction) 
            
            p_code = get_payer_code_by_name(patient.payer_name)
            raw_json = {}
            if p_code:
                res = perform_verification(p_code, patient.member_id, patient.first_name, patient.last_name, patient.dob)
                if res["success"]:
                    patient.status, patient.plan_type = "verified", res["plan_type"]
                    patient.copay, patient.coins = res["benefits"]["copay"], res["benefits"]["coins"]
                    patient.deductible_rem, patient.oop_rem = res["benefits"]["deductible"], res["benefits"]["oop"]
                    raw_json = res.get("raw_response")
                else: patient.status, raw_json = "error", res.get("raw_response")
            else: patient.status = "mapping_needed"
            
            if ENABLE_MEDICARE_SECONDARY_CHECK and "medicare" in (patient.payer_name or "").lower() and patient.sec_member_id:
                s_code = get_payer_code_by_name(patient.sec_payer_name)
                if s_code:
                    res_s = perform_verification(s_code, patient.sec_member_id, patient.first_name, patient.last_name, patient.dob)
                    if res_s["success"]:
                        if res_s["plan_type"]: patient.plan_type = f"{patient.plan_type} + {res_s['plan_type']}" if patient.plan_type else res_s['plan_type']
                        patient.copay, patient.coins = res_s["benefits"]["copay"], res_s["benefits"]["coins"]
                        patient.deductible_rem, patient.oop_rem = res_s["benefits"]["deductible"], res_s["benefits"]["oop"]
                        raw_json = res_s.get("raw_response")
            
            if raw_json:
                patient.pverify_raw = json.dumps(raw_json)

            db.session.add(patient); db.session.commit()
            return {"id": patient.id, "pverify_raw": raw_json, "gemini_raw": extraction, "filename": filename}
        finally: pass

@app.route('/uploads/<path:filename>')
def serve_uploaded_file(filename): return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/upload-single', methods=['POST'])
def upload_single():
    f = request.files.get('file')
    if f:
        unique_name = f"{time.time()}_{secure_filename(f.filename)}"
        path = os.path.join(app.config['UPLOAD_FOLDER'], unique_name); f.save(path)
        res = process_single_file(path, app, filename=unique_name)
        p = Patient.query.get(res["id"])
        return jsonify({"success":True, "patient": {"id": p.id, "first_name": p.first_name, "last_name": p.last_name, "dob": p.dob, "payer_name": p.payer_name, "plan_type": p.plan_type, "status": p.status, "copay": p.copay, "coins": p.coins, "deductible_rem": p.deductible_rem, "oop_rem": p.oop_rem, "pverify_raw": res.get("pverify_raw"), "gemini_raw": res.get("gemini_raw"), "filename": unique_name, "status_flag": p.status, "in_queue": p.in_queue}})
    return jsonify({"success": False, "error": "No file"})

@app.route('/batch-upload', methods=['POST'])
def batch_upload():
    if 'files' not in request.files: return jsonify({"error": "No files"}), 400
    files = request.files.getlist('files'); saved_paths = []
    for file in files:
        if file and allowed_file(file.filename):
            path = os.path.join(app.config['UPLOAD_FOLDER'], f"{datetime.now().timestamp()}_{secure_filename(file.filename)}"); file.save(path); saved_paths.append(path)
    if not saved_paths: return jsonify({"error": "No valid files"}), 400
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(process_single_file, path, app, os.path.basename(path)) for path in saved_paths]
        concurrent.futures.wait(futures)
    return jsonify({"success": True})

@app.route('/')
def index():
    if not session.get('role'): return render_template("index.html")
    patients = Patient.query.order_by(Patient.created_at.desc()).all()
    current_user = User.query.get(session.get('user_id'))
    available_locs = Location.query.all() if session.get('role') == 'admin' else current_user.locations
    return render_template("index.html", payers=load_payers(), patients=patients, current_user=current_user, available_locations=available_locs)

@app.route('/login', methods=['POST'])
def login():
    code = request.form.get('access_code')
    user = User.query.filter_by(access_code=code).first()
    if user:
        session['role'], session['name'], session['user_id'], session['permissions'] = user.role, user.name, user.id, user.permissions or ""
        return redirect(url_for('index'))
    return render_template("index.html", payers=load_payers(), login_error="Invalid Access Code")

@app.route('/api/set-location', methods=['POST'])
def set_active_location():
    if not session.get('user_id'): return jsonify({"success": False})
    loc_id = request.json.get('location_id')
    user = User.query.get(session['user_id'])
    if user:
        user.current_location_id = loc_id if loc_id else None
        db.session.commit()
        return jsonify({"success": True})
    return jsonify({"success": False})

@app.route('/admin')
def admin_dashboard():
    if session.get('role') != 'admin': return redirect(url_for('index'))
    codes = MedicalCode.query.all()
    pricing_data = Pricing.query.all()
    return render_template('admin.html', users=User.query.all(), locations=Location.query.all(), codes=codes, pricing_data=pricing_data, payers=load_payers())

@app.route('/api/admin/location', methods=['POST'])
def add_location():
    if session.get('role') != 'admin': return jsonify({"error": "Unauthorized"}), 403
    name, address = request.form.get('name'), request.form.get('address')
    if Location.query.filter_by(name=name).first(): return jsonify({"success": False, "error": "Location already exists"})
    db.session.add(Location(name=name, address=address)); db.session.commit()
    return jsonify({"success": True})

@app.route('/api/admin/user', methods=['POST'])
def manage_user():
    if session.get('role') != 'admin': return jsonify({"error": "Unauthorized"}), 403
    user_id, name, role, access_code = request.form.get('user_id'), request.form.get('name'), request.form.get('role'), request.form.get('access_code')
    location_ids, permissions = request.form.getlist('locations'), request.form.getlist('permissions')
    perm_string = ",".join(permissions)

    if user_id:
        user = User.query.get(user_id)
        if not user: return jsonify({"success": False, "error": "User not found"})
        user.name, user.role, user.access_code, user.permissions, user.locations = name, role, access_code, perm_string, []
        for lid in location_ids:
            loc = Location.query.get(int(lid))
            if loc: user.locations.append(loc)
    else:
        if User.query.filter_by(access_code=access_code).first(): return jsonify({"success": False, "error": "PIN already in use"})
        new_user = User(name=name, role=role, access_code=access_code, permissions=perm_string)
        for lid in location_ids:
            loc = Location.query.get(int(lid))
            if loc: new_user.locations.append(loc)
        db.session.add(new_user)
    db.session.commit(); return jsonify({"success": True})

@app.route('/api/admin/delete_user/<int:id>', methods=['POST'])
def delete_user_admin(id):
    if session.get('role') != 'admin': return jsonify({"error": "Unauthorized"}), 403
    u = User.query.get(id)
    if u: db.session.delete(u); db.session.commit()
    return jsonify({"success": True})

@app.route('/manual-add', methods=['POST'])
def manual_add():
    if not session.get('role'): return jsonify({"error": "Unauthorized"}), 403
    form = request.form
    verify_result = perform_verification(form.get("payerCode"), form.get("memberId"), form.get("firstName"), form.get("lastName"), form.get("dob"))
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
    if request.method == 'GET': return jsonify({m.pdf_payer_name: m.system_payer_name for m in PayerMapping.query.all()})
    data = request.json; pdf_name = data.get('pdf_name', '').strip().lower()
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
def logout(): session.clear(); return redirect(url_for('index'))


# ==========================================
# ENCOUNTER QUEUE & MA REVIEW ROUTES
# ==========================================

@app.route('/queue')
def encounter_queue():
    if not session.get('role'): return redirect(url_for('index'))
    all_patients = Patient.query.order_by(Patient.first_name.asc()).all()
    # ONLY SHOW IF IT HAS NOT BEEN SUBMITTED YET
    queue_patients = [p for p in all_patients if p.in_queue and p.encounter_status != 'submitted']
    providers = User.query.filter_by(role='provider').all()
    current_date = datetime.now().strftime("%A, %B %d, %Y")
    
    current_user = User.query.get(session['user_id'])
    available_locs = Location.query.all() if session.get('role') == 'admin' else current_user.locations
    
    loc_id = current_user.current_location_id
    for p in queue_patients:
        p.valid_codes = []
        if loc_id:
            search_payers = [p.payer_name]
            mapped = get_payer_code_by_name(p.payer_name)
            if mapped: search_payers.append(mapped)
            
            valid_pricing = Pricing.query.filter(
                Pricing.payer_id.in_(search_payers),
                Pricing.location_id == loc_id
            ).all()
            
            code_list = [pr.medical_code.code for pr in valid_pricing if pr.medical_code]
            code_list.sort(key=lambda x: (not x.startswith('99'), x))
            p.valid_codes = code_list
    
    return render_template("queue.html", all_patients=all_patients, queue_patients=queue_patients, providers=providers, current_date=current_date, current_user=current_user, available_locations=available_locs)

@app.route('/api/queue/add', methods=['POST'])
def api_queue_add():
    if not session.get('role'): return jsonify({"error": "Unauthorized"}), 403
    patient_ids = request.json.get('patient_ids', [])
    if patient_ids:
        Patient.query.filter(Patient.id.in_(patient_ids)).update({Patient.in_queue: True, Patient.encounter_status: 'pending'}, synchronize_session=False)
        db.session.commit()
    return jsonify({"success": True})

@app.route('/api/queue/remove/<int:id>', methods=['POST'])
def api_queue_remove(id):
    if not session.get('role'): return jsonify({"error": "Unauthorized"}), 403
    p = Patient.query.get(id)
    if p:
        p.in_queue = False
        db.session.commit()
    return jsonify({"success": True})

@app.route('/api/queue/submit', methods=['POST'])
def submit_encounter():
    if not session.get('role'): return jsonify({"error": "Unauthorized"}), 403
    data = request.json
    p = Patient.query.get(data.get('patient_id'))
    if p:
        p.encounter_status = 'submitted'
        p.encounter_total = data.get('total_cost', 0.0)
        p.patient_resp = data.get('patient_resp', 0.0)
        p.encounter_items = json.dumps(data.get('items', []))
        p.encounter_flag = data.get('flag', None)
        db.session.commit()
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Patient not found"})

@app.route('/ma-review')
def ma_review():
    if session.get('role') != 'admin': return redirect(url_for('index'))
    
    # Fetch patients who have had their queue submitted
    patients = Patient.query.filter_by(encounter_status='submitted').order_by(Patient.id.desc()).all()
    
    # Parse the saved JSON text back into a list of dictionaries for Jinja to render easily
    for p in patients:
        p.parsed_items = json.loads(p.encounter_items) if p.encounter_items else []
        
    current_date = datetime.now().strftime("%A, %B %d, %Y")
    return render_template("ma_review.html", patients=patients, current_date=current_date)


# ==========================================
# CODES AND PRICING ROUTES
# ==========================================

@app.route('/api/pricing/import', methods=['POST'])
def import_csv_pricing():
    if session.get('role') != 'admin': return jsonify({"error": "Unauthorized"}), 403
    payer_id = request.form.get('payer_id')
    location_id = request.form.get('location_id')
    file = request.files.get('file')
    
    if not payer_id or not location_id: return jsonify({"success": False, "error": "Payer and Location are required."})
    if not file or not file.filename.endswith('.csv'): return jsonify({"success": False, "error": "Please upload a valid .csv file."})

    try:
        stream = io.StringIO(file.stream.read().decode("UTF8", errors='ignore'), newline=None)
        csv_input = csv.reader(stream)
        records_processed = 0
        
        for row in csv_input:
            if len(row) >= 3:
                code_val = row[0].strip()
                desc_val = row[1].strip()
                price_str = row[2].strip().replace('$', '').replace(',', '')
                if not code_val or not price_str: continue
                
                try: price_val = float(price_str)
                except ValueError: continue 
                
                mc = MedicalCode.query.filter_by(code=code_val).first()
                if not mc:
                    mc = MedicalCode(code=code_val, description=desc_val)
                    db.session.add(mc)
                    db.session.flush() 
                
                pricing = Pricing.query.filter_by(payer_id=payer_id, code_id=mc.id, location_id=location_id).first()
                if pricing: pricing.price = price_val
                else:
                    pricing = Pricing(payer_id=payer_id, code_id=mc.id, location_id=location_id, price=price_val)
                    db.session.add(pricing)
                records_processed += 1
                
        db.session.commit()
        return jsonify({"success": True, "message": f"Successfully imported {records_processed} pricing records."})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route('/api/calculate-visit', methods=['POST'])
def calculate_visit():
    if not session.get('user_id'): return jsonify({"error": "Unauthorized"}), 403
    
    data = request.json
    patient_id = data.get('patient_id')
    selected_codes = data.get('codes', [])
    
    user = User.query.get(session['user_id'])
    loc_id = user.current_location_id
    
    if not loc_id:
        return jsonify({"error": "Provider Location Error: You must select a 'WORKING AT' facility location at the top of the screen before calculating pricing."}), 400
        
    patient = Patient.query.get(patient_id)
    if not patient: return jsonify({"error": "Patient not found"}), 404
    
    search_payers = [patient.payer_name] 
    mapped_code = get_payer_code_by_name(patient.payer_name)
    if mapped_code:
        search_payers.append(mapped_code)
    
    em_codes = []
    procedures = []
    
    for code in selected_codes:
        mc = MedicalCode.query.filter_by(code=code).first()
        price = 0.0
        desc = ""
        if mc:
            desc = mc.description
            pr = Pricing.query.filter(Pricing.payer_id.in_(search_payers), Pricing.code_id == mc.id, Pricing.location_id == loc_id).first()
            if pr: price = pr.price
            
        is_em = code.startswith('99')
        item = {
            "code": code, 
            "desc": desc,
            "base_price": price, 
            "final_price": price, 
            "type": "E&M" if is_em else "PROC", 
            "discounted": False
        }
        
        if is_em: em_codes.append(item)
        else: procedures.append(item)
            
    procedures.sort(key=lambda x: x["base_price"], reverse=True)
    for i, proc in enumerate(procedures):
        if i > 0 and proc["base_price"] > 0:
            proc["final_price"] = proc["base_price"] * 0.5
            proc["discounted"] = True
            
    final_list = em_codes + procedures
    total_visit_cost = sum(item["final_price"] for item in final_list)
    
    try:
        ded_str = re.sub(r'[^\d.]', '', str(patient.deductible_rem))
        ded_rem = float(ded_str) if ded_str else 0.0
    except:
        ded_rem = 0.0
        
    try:
        copay_str = re.sub(r'[^\d.]', '', str(patient.copay))
        copay = float(copay_str) if copay_str else 0.0
    except:
        copay = 0.0

    if ded_rem > 0:
        patient_resp = min(total_visit_cost, ded_rem)
    else:
        patient_resp = min(total_visit_cost, copay)

    return jsonify({
        "success": True, 
        "items": final_list, 
        "total_cost": total_visit_cost,
        "patient_resp": patient_resp
    })

if __name__ == "__main__": app.run(host="0.0.0.0", port=8081)