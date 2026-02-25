from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

# ... (Keep User and PayerMapping classes as they are) ...

class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    access_code = db.Column(db.String(50), unique=True, nullable=False)
    role = db.Column(db.String(20), nullable=False)
    name = db.Column(db.String(100), nullable=False)

class PayerMapping(db.Model):
    __tablename__ = 'payer_mappings'
    id = db.Column(db.Integer, primary_key=True)
    pdf_payer_name = db.Column(db.String(255), unique=True, nullable=False)
    system_payer_name = db.Column(db.String(255), nullable=False)

class Patient(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())
    plan_type = db.Column(db.String(100))
    # Demographics
    first_name = db.Column(db.String(100))
    last_name = db.Column(db.String(100))
    dob = db.Column(db.String(20))
    
    # --- PRIMARY INSURANCE ---
    member_id = db.Column(db.String(50))
    payer_name = db.Column(db.String(200))
    status = db.Column(db.String(50))
    copay = db.Column(db.String(50), default="$0.00")
    coins = db.Column(db.String(50), default="0%")
    deductible_rem = db.Column(db.String(50), default="$0.00")
    oop_rem = db.Column(db.String(50), default="$0.00")
    
    # --- SECONDARY INSURANCE (NEW) ---
    sec_member_id = db.Column(db.String(50))
    sec_payer_name = db.Column(db.String(200))
    sec_status = db.Column(db.String(50))  # 'verified', 'skipped', 'error'
    sec_copay = db.Column(db.String(50), default="")
    sec_coins = db.Column(db.String(50), default="")
    sec_deductible_rem = db.Column(db.String(50), default="")
    sec_oop_rem = db.Column(db.String(50), default="")