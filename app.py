from flask import Flask, render_template, request, jsonify, session, redirect, url_for
import requests
import json
import os
import urllib3
from datetime import datetime
from werkzeug.utils import secure_filename
from readpdf import extract_from_referral
from models import db, User, PayerMapping, Patient 
from flask_migrate import Migrate
import concurrent.futures

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

# ================= DB CONFIGURATION =================
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get(
    "DATABASE_URL", 
    "mysql+mysqlconnector://dev:Admin%401234@10.91.0.128/pverifyDB" 
)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)
migrate = Migrate(app, db)

with app.app_context():
    db.create_all()
    if not User.query.filter_by(access_code='7432').first():
        db.session.add(User(access_code='7432', role='admin', name='Dr. Amit'))
        db.session.add(User(access_code='2262', role='provider', name='Provider/PA'))
        db.session.add(User(access_code='1234', role='checkout', name='Checkout Desk'))
        db.session.commit()

UPLOAD_FOLDER = 'uploads'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

ELIGIBILITY_API_URL = os.environ.get(
    "ELIGIBILITY_API_URL",
    "https://api.insuranceclaim.urtestsite.com/api/check-eligibility"
)

# ================= HELPER FUNCTIONS =================

def load_payers():
    try:
        with open("payers_output.json", "r", encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        return []

def fmt_percent(val):
    if val is None: return None
    s_val = str(val)
    if s_val == "0": return "0%"
    if "." in s_val:
        try: return f"{float(s_val)*100:.0f}%"
        except: return s_val
    return s_val

def perform_verification(payer_code, member_id, first_name, last_name, dob):
    try:
        static_provider_last = "Corium Ventures Pllc"
        static_npi = "1346553120"
        static_dos = datetime.now().strftime("%m/%d/%Y")
        
        payload = {
            "PayerCode": payer_code,
            "DOS_StartDate": static_dos,
            "DOS_EndDate": static_dos,
            "IsSubscriberPatient": True,
            "RequestingProvider": {
                "ProviderType": "Billing",
                "LastName": static_provider_last,
                "NPI": static_npi
            },
            "Subscriber": {
                "MemberID": member_id,
                "FirstName": first_name,
                "LastName": last_name,
                "DOB": dob
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

            spec_data = {"copay": "0.00", "coins": fmt_percent(plan_coins) or "0%", "auth": None}
            surg_data = {"coins": fmt_percent(plan_coins) or "0%", "auth": None}

            for s in services:
                name = s.get("ServiceName", "").lower()
                details = s.get("EligibilityDetails") or []
                for d in details:
                    benefit_type = d.get("EligibilityOrBenefit")
                    if "professional" in name or "office" in name:
                        if benefit_type == "Co-Payment":
                            val = d.get("MonetaryAmount")
                            if val is not None:
                                try: spec_data["copay"] = f"{float(val):.2f}"
                                except: spec_data["copay"] = str(val)
                        if benefit_type == "Co-Insurance":
                            val = d.get("Percent")
                            if val is not None: spec_data["coins"] = fmt_percent(val)
            
            return {
                "success": True,
                "payer_name": api_data.get("PayerName", "Unknown Payer"),
                "is_hmo": api_data.get("IsHMOPlan"),
                "benefits": {
                    "specialist": spec_data,
                    "surgical": surg_data,
                    "oop": {
                        "indiv_deduct_rem": (oop.get("IndividualDeductibleRemainingInNet") or {}).get("Value") or "$0.00",
                        "indiv_oop_rem": (oop.get("IndividualOOPRemainingInNet") or {}).get("Value") or "$0.00"
                    }
                }
            }
        else:
            return {"success": False, "error": f"API Error: {response.status_code}"}
    except Exception as e:
        return {"success": False, "error": str(e)}

def process_single_file(filepath, app_context_app):
    with app_context_app.app_context():
        form = {"first_name": "Manual", "last_name": "Review", "dob": "", "member_id": "", "payer_name": "Unreadable File"}
        pdf_payer = "Manual Review Required"
        
        try:
            extraction = extract_from_referral(filepath)
            if isinstance(extraction, list): extraction = extraction[0] if len(extraction) > 0 else {}
            if isinstance(extraction, dict) and "error" not in extraction:
                extracted_form = extraction.get("form_population_data", {})
                if extracted_form:
                    form = extracted_form
                    pdf_payer = form.get("payer_name", "Unknown Payer").strip()
        except: pass
        finally:
            if os.path.exists(filepath): os.remove(filepath)

        system_payer_code = None
        mapping = PayerMapping.query.filter_by(pdf_payer_name=pdf_payer.lower()).first()
        if mapping:
            parts = mapping.system_payer_name.split(' - ')
            if len(parts) > 1: system_payer_code = parts[-1]
        
        if not system_payer_code:
            all_payers = load_payers()
            for p in all_payers:
                if p['payerName'].lower() == pdf_payer.lower():
                    system_payer_code = p['payerCode']; break

        new_patient = Patient(
            first_name=form.get("first_name"),
            last_name=form.get("last_name"),
            dob=form.get("dob"),
            member_id=form.get("member_id"),
            payer_name=pdf_payer
        )

        if system_payer_code:
            verify_result = perform_verification(system_payer_code, form.get("member_id"), form.get("first_name"), form.get("last_name"), form.get("dob"))
            if verify_result["success"]:
                new_patient.status = "verified"
                new_patient.payer_name = verify_result["payer_name"]
                ben = verify_result["benefits"]
                copay = ben['specialist']['copay']
                if copay and '$' not in str(copay): copay = f"${copay}"
                new_patient.copay = copay
                new_patient.coins = ben['surgical']['coins']
                new_patient.deductible_rem = ben['oop']['indiv_deduct_rem']
                new_patient.oop_rem = ben['oop']['indiv_oop_rem']
            else:
                new_patient.status = "error"
        else:
            new_patient.status = "mapping_needed"

        db.session.add(new_patient)
        db.session.commit()
        return {"id": new_patient.id}

# ================= ROUTES =================

@app.route('/login', methods=['POST'])
def login():
    code = request.form.get('access_code')
    user = User.query.filter_by(access_code=code).first()
    if user:
        session['role'] = user.role
        session['name'] = user.name
        return redirect(url_for('index'))
    return render_template("index.html", payers=load_payers(), login_error="Invalid Access Code")

@app.route('/')
def index():
    if not session.get('role'): return render_template("index.html")
    patients = Patient.query.order_by(Patient.created_at.desc()).all()
    return render_template("index.html", payers=load_payers(), patients=patients)

@app.route('/manual-add', methods=['POST'])
def manual_add():
    if not session.get('role'): return jsonify({"error": "Unauthorized"}), 403
    form = request.form
    payer_code = form.get("payerCode")
    
    verify_result = perform_verification(
        payer_code, form.get("memberId"), form.get("firstName"), form.get("lastName"), form.get("dob")
    )
    
    new_patient = Patient(
        first_name=form.get("firstName"),
        last_name=form.get("lastName"),
        dob=form.get("dob"),
        member_id=form.get("memberId"),
        payer_name=form.get("payerDisplayInput", "Manual Payer")
    )

    if verify_result["success"]:
        new_patient.status = "verified"
        new_patient.payer_name = verify_result["payer_name"]
        ben = verify_result["benefits"]
        copay = ben['specialist']['copay']
        if copay and '$' not in str(copay): copay = f"${copay}"
        new_patient.copay = copay
        new_patient.coins = ben['surgical']['coins']
        new_patient.deductible_rem = ben['oop']['indiv_deduct_rem']
        new_patient.oop_rem = ben['oop']['indiv_oop_rem']
    else:
        new_patient.status = "error"

    db.session.add(new_patient)
    db.session.commit()
    return redirect(url_for('index'))

@app.route('/delete-patient/<int:id>', methods=['POST'])
def delete_patient(id):
    if not session.get('role'): return jsonify({"error": "Unauthorized"}), 403
    p = Patient.query.get(id)
    if p:
        db.session.delete(p)
        db.session.commit()
        return jsonify({"success": True})
    return jsonify({"error": "Not found"}), 404

# --- NEW: DELETE ALL ROUTE ---
@app.route('/delete-all-patients', methods=['POST'])
def delete_all_patients():
    if not session.get('role'): return jsonify({"error": "Unauthorized"}), 403
    try:
        db.session.query(Patient).delete()
        db.session.commit()
        return jsonify({"success": True})
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

@app.route('/batch-upload', methods=['POST'])
def batch_upload():
    if 'files' not in request.files: return jsonify({"error": "No files"}), 400
    files = request.files.getlist('files')
    saved_paths = []
    for file in files:
        if file.filename:
            path = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(file.filename))
            file.save(path)
            saved_paths.append(path)
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(process_single_file, path, app) for path in saved_paths]
        concurrent.futures.wait(futures)
            
    return jsonify({"success": True})

@app.route('/api/mappings', methods=['GET', 'POST', 'DELETE'])
def handle_mappings():
    if not session.get('role'): return jsonify({"error": "Unauthorized"}), 403
    if request.method == 'GET':
        mappings = PayerMapping.query.all()
        return jsonify({m.pdf_payer_name: m.system_payer_name for m in mappings})
    if request.method == 'POST':
        data = request.json
        pdf_name, sys_name = data.get('pdf_name', '').strip().lower(), data.get('system_name', '').strip()
        if not pdf_name or not sys_name: return jsonify({"error": "Invalid"}), 400
        mapping = PayerMapping.query.filter_by(pdf_payer_name=pdf_name).first()
        if mapping: mapping.system_payer_name = sys_name
        else: db.session.add(PayerMapping(pdf_payer_name=pdf_name, system_payer_name=sys_name))
        db.session.commit()
        return jsonify({"success": True})
    if request.method == 'DELETE':
        data = request.json
        mapping = PayerMapping.query.filter_by(pdf_payer_name=data.get('pdf_name', '').strip().lower()).first()
        if mapping: db.session.delete(mapping); db.session.commit()
        return jsonify({"success": True})

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8081)