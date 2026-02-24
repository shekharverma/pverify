import logging
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
from sqlalchemy import func

# --- 1. SETUP LOGGING ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

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

# --- 2. OPTIMIZE DB POOL FOR THREADING ---
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_size': 20,       # Allow 20 simultaneous connections
    'max_overflow': 10,    # Allow 10 extra bursts
    'pool_recycle': 3600,  # Refresh connections every hour
}

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
ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg', 'webp'}

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

ELIGIBILITY_API_URL = os.environ.get(
    "ELIGIBILITY_API_URL",
    "https://api.insuranceclaim.urtestsite.com/api/check-eligibility"
)

# ================= HELPER FUNCTIONS =================

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def load_payers():
    try:
        with open("payers_output.json", "r", encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return []

def fmt_percent(val):
    if val is None: return None
    s_val = str(val)
    if s_val == "0": return "0%"
    if "." in s_val:
        try: return f"{float(s_val)*100:.0f}%"
        except: return s_val
    return s_val

def get_payer_code_by_name(name_to_find):
    if not name_to_find: return None
    name_clean = name_to_find.strip().lower()
    
    # Check DB Mapping
    try:
        mapping = PayerMapping.query.filter(func.lower(PayerMapping.pdf_payer_name) == name_clean).first()
        if mapping:
            parts = mapping.system_payer_name.split(' - ')
            if len(parts) > 1: 
                logger.info(f"✅ Mapping Found: '{name_to_find}' -> {parts[-1].strip()}")
                return parts[-1].strip()
    except Exception as e:
        logger.error(f"Mapping DB Error: {e}")

    # Check JSON List
    all_payers = load_payers()
    for p in all_payers:
        if p.get('payerName', '').strip().lower() == name_clean:
            logger.info(f"✅ Direct JSON Match: '{name_to_find}'")
            return p.get('payerCode')
    
    logger.warning(f"❌ No Payer Code found for: '{name_to_find}'")
    return None

def perform_verification(payer_code, member_id, first_name, last_name, dob):
    # FAST FAIL: Don't call API if member_id is missing
    if not member_id or str(member_id).strip() in ["", "None", "null"]:
        logger.warning(f"⚠️ Verification Skipped: Missing Member ID for {first_name} {last_name}")
        return {"success": False, "error": "Missing Member ID on document"}

    try:
        static_dos = datetime.now().strftime("%m/%d/%Y")
        payload = {
            "PayerCode": payer_code,
            "DOS_StartDate": static_dos,
            "DOS_EndDate": static_dos,
            "IsSubscriberPatient": True,
            "RequestingProvider": {"ProviderType": "Billing", "LastName": "Corium Ventures Pllc", "NPI": "1346553120"},
            "Subscriber": {"MemberID": str(member_id).strip(), "FirstName": first_name, "LastName": last_name, "DOB": dob}
        }
        
        # --- 3. INCREASED TIMEOUT TO 60 SECONDS ---
        response = requests.post(ELIGIBILITY_API_URL, json=payload, verify=False, timeout=60)
        
        if response.status_code == 200:
            api_data = response.json()
            oop = api_data.get("HBPC_Deductible_OOP_Summary") or {}
            dme = api_data.get("DMESummary") or {}
            plan_coins = (dme.get("CoInsInNet") or {}).get("Value")
            
            return {
                "success": True,
                "payer_name": api_data.get("PayerName", "Unknown"),
                "benefits": {
                    "copay": "0.00", 
                    "coins": fmt_percent(plan_coins) or "0%",
                    "deductible": (oop.get("IndividualDeductibleRemainingInNet") or {}).get("Value") or "$0.00",
                    "oop": (oop.get("IndividualOOPRemainingInNet") or {}).get("Value") or "$0.00"
                }
            }
        return {"success": False, "error": f"API Error: {response.status_code}"}
    except requests.exceptions.Timeout:
        logger.error(f"⏳ Verification TIMEOUT for member {member_id}")
        return {"success": False, "error": "Verification Timed Out"}
    except Exception as e:
        logger.error(f"💥 Verification Exception: {str(e)}")
        return {"success": False, "error": str(e)}

# --- WORKER THREAD ---
def process_single_file(filepath, app_context_app):
    with app_context_app.app_context():
        # Setup default fallback
        form = {"first_name": "Manual", "last_name": "Review", "dob": "", "member_id": "", "payer_name": "Unreadable File"}
        raw_pdf_payer = "Unknown Payer"
        
        try:
            logger.info(f"📄 Processing file: {os.path.basename(filepath)}")
            
            # 1. AI Extraction
            try:
                extraction = extract_from_referral(filepath)
                if isinstance(extraction, list): extraction = extraction[0] if len(extraction) > 0 else {}
                
                if isinstance(extraction, dict) and "error" not in extraction:
                    extracted_form = extraction.get("form_population_data", {})
                    if extracted_form:
                        form = extracted_form
                        raw_pdf_payer = form.get("payer_name", "Unknown Payer").strip()
                        logger.info(f"🤖 AI Extracted Payer: {raw_pdf_payer}")
            except Exception as e:
                logger.error(f"AI Extraction Failed: {e}")

            # 2. Payer Lookup
            system_payer_code = get_payer_code_by_name(raw_pdf_payer)

            # 3. Create Patient Record
            new_patient = Patient(
                first_name=form.get("first_name"),
                last_name=form.get("last_name"),
                dob=form.get("dob"),
                member_id=form.get("member_id"),
                payer_name=raw_pdf_payer  # Save Raw Name initially so user sees it
            )

            # 4. Verify if Payer Found
            if system_payer_code:
                logger.info(f"🔄 Verifying Eligibility for {form.get('first_name')} with PayerCode {system_payer_code}...")
                verify_result = perform_verification(
                    system_payer_code, 
                    form.get("member_id"), 
                    form.get("first_name"), 
                    form.get("last_name"), 
                    form.get("dob")
                )
                
                if verify_result["success"]:
                    logger.info("✅ Verification SUCCESS")
                    new_patient.status = "verified"
                    new_patient.payer_name = verify_result["payer_name"] # Update to official name
                    new_patient.copay = verify_result["benefits"].get("copay")
                    new_patient.coins = verify_result["benefits"].get("coins")
                    new_patient.deductible_rem = verify_result["benefits"].get("deductible")
                    new_patient.oop_rem = verify_result["benefits"].get("oop")
                else:
                    logger.warning(f"⚠️ Verification FAILED: {verify_result.get('error')}")
                    new_patient.status = "error"
            else:
                logger.info("⚠️ No Payer Mapping Found -> Status: Mapping Needed")
                new_patient.status = "mapping_needed"

            db.session.add(new_patient)
            db.session.commit()
            return {"id": new_patient.id}

        except Exception as e:
            logger.critical(f"🔥 CRITICAL THREAD ERROR: {e}")
            return {"error": str(e)}
        finally:
            # Cleanup
            db.session.remove()
            if os.path.exists(filepath): os.remove(filepath)

# ================= ROUTES =================

@app.route('/batch-upload', methods=['POST'])
def batch_upload():
    if 'files' not in request.files: return jsonify({"error": "No files"}), 400
    files = request.files.getlist('files')
    saved_paths = []
    
    for file in files:
        if file and allowed_file(file.filename):
            path = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(file.filename))
            file.save(path)
            saved_paths.append(path)
    
    if not saved_paths:
        return jsonify({"error": "No valid files uploaded"}), 400

    logger.info(f"🚀 Starting Batch Process for {len(saved_paths)} files...")
    
    # --- 4. CONTROL CONCURRENCY TO PREVENT API OVERLOAD ---
    # Reduced to 5 max workers. The external API cannot handle 20 requests at once and will drop connection.
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(process_single_file, path, app) for path in saved_paths]
        concurrent.futures.wait(futures)
            
    logger.info("🏁 Batch Process Complete.")
    return jsonify({"success": True})

@app.route('/upload-single', methods=['POST'])
def upload_single():
    """Processes a single file to prevent timeouts and update UI incrementally."""
    if not session.get('role'):
        return jsonify({"success": False, "error": "Unauthorized"}), 403
    
    if 'file' not in request.files:
        return jsonify({"success": False, "error": "No file"}), 400
    
    file = request.files['file']
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(path)
        
        try:
            # Process the file using your existing thread logic
            result = process_single_file(path, app)
            
            if "id" in result:
                p = Patient.query.get(result["id"])
                return jsonify({
                    "success": True,
                    "patient": {
                        "id": p.id,
                        "first_name": p.first_name,
                        "last_name": p.last_name,
                        "dob": p.dob,
                        "payer_name": p.payer_name,
                        "status": p.status,
                        "copay": p.copay,
                        "coins": p.coins,
                        "deductible_rem": p.deductible_rem,
                        "oop_rem": p.oop_rem
                    }
                })
            return jsonify({"success": False, "error": result.get("error")})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})
            
    return jsonify({"success": False, "error": "Invalid file type"}), 400

@app.route('/login', methods=['POST'])
def login():
    code = request.form.get('access_code')
    user = User.query.filter_by(access_code=code).first()
    if user:
        session['role'], session['name'] = user.role, user.name
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
    verify_result = perform_verification(payer_code, form.get("memberId"), form.get("firstName"), form.get("lastName"), form.get("dob"))
    new_patient = Patient(first_name=form.get("firstName"), last_name=form.get("lastName"), dob=form.get("dob"), member_id=form.get("memberId"), payer_name=form.get("payerDisplayInput"))
    if verify_result["success"]:
        new_patient.status, new_patient.payer_name = "verified", verify_result["payer_name"]
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
    if not session.get('role'): return jsonify({"error": "Unauthorized"}), 403
    if request.method == 'GET':
        mappings = PayerMapping.query.all()
        return jsonify({m.pdf_payer_name: m.system_payer_name for m in mappings})
    if request.method == 'POST':
        data = request.json
        pdf_name = data.get('pdf_name', '').strip().lower()
        sys_name = data.get('system_name', '').strip()
        if not pdf_name or not sys_name: return jsonify({"error": "Invalid"}), 400
        mapping = PayerMapping.query.filter_by(pdf_payer_name=pdf_name).first()
        if mapping: mapping.system_payer_name = sys_name
        else: db.session.add(PayerMapping(pdf_payer_name=pdf_name, system_payer_name=sys_name))
        db.session.commit()
        return jsonify({"success": True})
    if request.method == 'DELETE':
        data = request.json
        pdf_name = data.get('pdf_name', '').strip().lower()
        mapping = PayerMapping.query.filter_by(pdf_payer_name=pdf_name).first()
        if mapping: db.session.delete(mapping); db.session.commit()
        return jsonify({"success": True})

@app.route('/change-code', methods=['POST'])
def change_code():
    if not session.get('role'): return jsonify({"success": False, "error": "Not authenticated"})
    current_code = request.form.get('current_code')
    new_code = request.form.get('new_code')
    user = User.query.filter_by(access_code=current_code).first()
    if not user: return jsonify({"success": False, "error": "Incorrect current PIN"})
    if User.query.filter_by(access_code=new_code).first(): return jsonify({"success": False, "error": "PIN in use"})
    user.access_code = new_code
    db.session.commit()
    return jsonify({"success": True})

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8081)