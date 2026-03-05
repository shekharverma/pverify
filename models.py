from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

# --- Association Table for User <-> Location ---
user_locations = db.Table('user_locations',
    db.Column('user_id', db.Integer, db.ForeignKey('users.id'), primary_key=True),
    db.Column('location_id', db.Integer, db.ForeignKey('locations.id'), primary_key=True)
)

# --- Location Model ---
class Location(db.Model):
    __tablename__ = 'locations'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    address = db.Column(db.String(255))

# --- User Model ---
class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    access_code = db.Column(db.String(50), unique=True, nullable=False)
    role = db.Column(db.String(20), nullable=False) # 'admin', 'staff', 'provider'
    name = db.Column(db.String(100), nullable=False)
    permissions = db.Column(db.String(255), default="all")
    
    current_location_id = db.Column(db.Integer, db.ForeignKey('locations.id'), nullable=True)
    current_location = db.relationship('Location', foreign_keys=[current_location_id])
    
    locations = db.relationship('Location', secondary=user_locations, lazy='subquery',
        backref=db.backref('users', lazy=True))

# --- Payer Mapping Model ---
class PayerMapping(db.Model):
    __tablename__ = 'payer_mappings'
    id = db.Column(db.Integer, primary_key=True)
    pdf_payer_name = db.Column(db.String(255), unique=True, nullable=False)
    system_payer_name = db.Column(db.String(255), nullable=False)

# --- Patient Model ---
class Patient(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())
    
    file_path = db.Column(db.String(255), nullable=True)
    pverify_raw = db.Column(db.Text, nullable=True) 
    sec_pverify_raw = db.Column(db.Text, nullable=True) # NEW: Store Secondary JSON
    gemini_raw = db.Column(db.Text, nullable=True)

    plan_type = db.Column(db.String(100))
    first_name = db.Column(db.String(100))
    last_name = db.Column(db.String(100))
    dob = db.Column(db.String(20))
    member_id = db.Column(db.String(50))
    payer_name = db.Column(db.String(200))
    status = db.Column(db.String(50))
    copay = db.Column(db.String(50), default="$0.00")
    coins = db.Column(db.String(50), default="0%")
    deductible_rem = db.Column(db.String(50), default="$0.00")
    oop_rem = db.Column(db.String(50), default="$0.00")
    
    sec_member_id = db.Column(db.String(50))
    sec_payer_name = db.Column(db.String(200))
    sec_status = db.Column(db.String(50))
    sec_copay = db.Column(db.String(50), default="")
    sec_coins = db.Column(db.String(50), default="")
    sec_deductible_rem = db.Column(db.String(50), default="")
    sec_oop_rem = db.Column(db.String(50), default="")
    
    in_queue = db.Column(db.Boolean, default=False)
    
    encounter_status = db.Column(db.String(50), default="pending") 
    encounter_total = db.Column(db.Float, default=0.0)
    patient_resp = db.Column(db.Float, default=0.0)
    encounter_items = db.Column(db.Text, nullable=True) 
    encounter_flag = db.Column(db.String(20), nullable=True) 

# ==========================================
# MEDICAL CODES & PRICING MODELS
# ==========================================

class MedicalCode(db.Model):
    __tablename__ = 'medical_codes'
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(50), unique=True, nullable=False)
    description = db.Column(db.String(255))
    is_add_on = db.Column(db.Boolean, default=False)

class Pricing(db.Model):
    __tablename__ = 'pricing'
    id = db.Column(db.Integer, primary_key=True)
    payer_id = db.Column(db.String(100), nullable=False) 
    code_id = db.Column(db.Integer, db.ForeignKey('medical_codes.id'), nullable=False)
    location_id = db.Column(db.Integer, db.ForeignKey('locations.id'), nullable=False)
    price = db.Column(db.Float, nullable=False, default=0.0)

    medical_code = db.relationship('MedicalCode', backref=db.backref('pricing_records', lazy=True))
    location = db.relationship('Location', backref=db.backref('pricing_records', lazy=True))

# ==========================================
# VISIT CALCULATOR TRACKING (NEW)
# ==========================================
class SavedVisit(db.Model):
    __tablename__ = 'saved_visits'
    id = db.Column(db.Integer, primary_key=True)
    provider_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    patient_id = db.Column(db.Integer, db.ForeignKey('patient.id'), nullable=True)
    visit_date = db.Column(db.String(20), nullable=False)
    total_visit = db.Column(db.Float, default=0.0)
    patient_responsibility = db.Column(db.Float, default=0.0)
    insurance_contribution = db.Column(db.Float, default=0.0)
    codes_json = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())

    provider = db.relationship('User', backref=db.backref('saved_visits', lazy=True))
    patient = db.relationship('Patient', backref=db.backref('saved_visits', lazy=True))